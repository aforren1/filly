// filly for the web: filly's renderer, compiled to WebAssembly, drawing into a page's WebGL2
// context that the page's own renderer (PIXI in PsychoJS) also uses. Filament renders into a
// texture that the page owns, and the page draws that texture; nothing is copied.
//
// The page and Filament each cache GL state in one context. filly enters the context before its
// first call after the page drew, and hands it back before the page draws again:
//   enter     sets the pixel-store state to GL defaults, unbinds the page's vertex array object
//             (Filament assumes the default one and would otherwise rebind the page's
//             buffers), and makes Filament set all of its state again.
//   hand back unbinds what Filament leaves bound (samplers and textures on the units that it
//             used, buffers, framebuffers), restores GL defaults that the page may rely on,
//             including the pixel-store state, then calls the page's onHandBack hook (PIXI:
//             renderer.reset()). The page's own pixel-store values are not kept.
// Call renderer.handBack() before the page draws, or hook it into the page's frame. Property
// reads do not enter the context.
//
// API: createRenderer(gl, options) -> Renderer. The classes follow filly's Python API
// (docs/reference/api.md) with JavaScript names; option objects replace keyword arguments.
// Vectors are arrays. Transforms are row-major arrays of 16 numbers for column vectors: the
// translation is in elements 3, 7, and 11, as in matrix[:3, 3] in Python.

import createFillyModule from "./filly-core.js";

export class FillyError extends Error
{
	constructor(message)
	{
		super(message);
		this.name = new.target.name;
	}
}
export class BackendError extends FillyError {}
export class AssetError extends FillyError {}
export class InteropError extends FillyError {}

const ERROR_TYPES = {
	"filly::FillyError": FillyError,
	"filly::BackendError": BackendError,
	"filly::AssetError": AssetError,
	"filly::InteropError": InteropError,
	"std::invalid_argument": RangeError,
	"std::out_of_range": RangeError,
};

let modulePromise = null;
let loadedModule = null;

/** Load the WebAssembly module once per page. filly-core.wasm is resolved next to this file. */
export function loadModule()
{
	modulePromise ??= createFillyModule({ locateFile: (file) => new URL(file, import.meta.url).href })
		.then((module) => (loadedModule = module));
	return modulePromise;
}

function moduleNow()
{
	if (!loadedModule)
	{
		throw new FillyError("filly is not loaded yet: await createRenderer() or loadModule() first");
	}
	return loadedModule;
}

/**
 * Vertex arrays for simple shapes, for Scene.createMesh({ ...shape, ... }): flat Float32Array
 * positions and normals (3 per vertex) and uvs (2 per vertex), and Uint32Array indices (3 per
 * triangle). Front faces wind counterclockwise; UV (0, 0) is the top left of an image. Needs
 * the loaded module.
 */
export const shapes = {
	/** A rectangle in the XY plane facing +Z. segments is [columns, rows]. */
	plane: ({ width = 1, height = 1, segments = [1, 1] } = {}) =>
		callNative(moduleNow(), () => moduleNow().shapePlane(width, height, segments[0], segments[1])),
	/** An axis-aligned box; each face has its own vertices and full UVs. */
	box: ({ width = 1, height = 1, depth = 1 } = {}) =>
		callNative(moduleNow(), () => moduleNow().shapeBox(width, height, depth)),
	/** A sphere with poles on Y. */
	uvSphere: ({ radius = 0.5, segments = 32, rings = 16 } = {}) =>
		callNative(moduleNow(), () => moduleNow().shapeUvSphere(radius, segments, rings)),
	/** A cylinder on the Y axis. */
	cylinder: ({ radius = 0.5, height = 1, segments = 32, caps = true } = {}) =>
		callNative(moduleNow(), () => moduleNow().shapeCylinder(radius, height, segments, caps)),
};

/** Set the minimum Filament log severity: "verbose", "debug", "info", "warning", "error", "off". */
export async function setLogLevel(level)
{
	const module = await loadModule();
	callNative(module, () => module.setLogLevel(level));
}

function callNative(module, fn)
{
	try
	{
		return fn();
	}
	catch (error)
	{
		throw nativeError(module, error);
	}
}

function nativeError(module, error)
{
	if (typeof WebAssembly.Exception === "function" && error instanceof WebAssembly.Exception)
	{
		const [type, message] = module.getExceptionMessage(error);
		module.decrementExceptionRefcount(error);
		const ErrorType = ERROR_TYPES[type] ?? FillyError;
		return new ErrorType(message);
	}
	return error;
}

// Pixel-store state is context state that both sides set; PIXI sets the WebGL-only flags.
// Filament assumes the GL defaults of these.
const PIXEL_STORE = ["UNPACK_FLIP_Y_WEBGL", "UNPACK_PREMULTIPLY_ALPHA_WEBGL",
	"UNPACK_COLORSPACE_CONVERSION_WEBGL", "UNPACK_IMAGE_HEIGHT", "UNPACK_SKIP_PIXELS", "UNPACK_SKIP_ROWS",
	"UNPACK_SKIP_IMAGES", "PACK_SKIP_PIXELS", "PACK_SKIP_ROWS", "UNPACK_ALIGNMENT", "UNPACK_ROW_LENGTH",
	"PACK_ALIGNMENT", "PACK_ROW_LENGTH"];
// The bits that web/gl_tracking.js records for the texture targets that the core binds.
const TEXTURE_TARGETS = ["TEXTURE_2D", "TEXTURE_CUBE_MAP", "TEXTURE_2D_ARRAY", "TEXTURE_3D"];

class Context
{
	constructor(module, gl)
	{
		if (typeof WebGL2RenderingContext === "undefined" || !(gl instanceof WebGL2RenderingContext))
		{
			throw new InteropError("filly needs a WebGL2 context");
		}
		this.module = module;
		this.gl = gl;
		this.handle = module.GL.registerContext(gl, { majorVersion: 2, minorVersion: 0, enableExtensionsByDefault: true });
		this.renderers = new Set();
		this.onHandBack = new Set();
		this.entered = false;
		const units = gl.getParameter(gl.MAX_COMBINED_TEXTURE_IMAGE_UNITS);
		// What the core changes while it has the context; web/gl_tracking.js fills it in.
		this.tracking = module.GL.contexts[this.handle].filly = {
			unit: 0,
			targets: new Uint8Array(units),  // bits of TEXTURE_TARGETS bound on each unit
			samplers: new Uint8Array(units),
			dirty: [],  // units with a texture or a sampler
			pixelStore: new Map(),  // parameter -> last value
		};
		this.textureTargets = TEXTURE_TARGETS.map((name) => gl[name]);
		this.bufferTargets = [gl.ARRAY_BUFFER, gl.UNIFORM_BUFFER, gl.PIXEL_PACK_BUFFER, gl.PIXEL_UNPACK_BUFFER,
			gl.COPY_READ_BUFFER, gl.COPY_WRITE_BUFFER];
		this.pixelStore = PIXEL_STORE.map((name) => gl[name]);
		this.pixelStoreDefaults = PIXEL_STORE.map((name) => name.endsWith("_ALIGNMENT") ? 4
			: name === "UNPACK_COLORSPACE_CONVERSION_WEBGL" ? gl.BROWSER_DEFAULT_WEBGL : 0);
		// Hand back must restore the page's pixel-store state, because a page renderer may cache
		// it (Babylon.js caches UNPACK_FLIP_Y_WEBGL). Querying it in each enter costs a round trip
		// to the GPU process (see below), so it is read once here, and the page's later sets are
		// recorded by a wrapper on this context. The core's sets bypass the wrapper
		// (web/gl_tracking.js). Looked up at each call, so that wrappers that a page installs on
		// the prototype later still see filly's calls.
		const own = Object.prototype.hasOwnProperty.call(gl, "pixelStorei") ? gl.pixelStorei : null;
		const setPixelStore = (name, value) => (own ?? WebGL2RenderingContext.prototype.pixelStorei).call(gl, name, value);
		this.setPixelStore = this.tracking.setPixelStore = setPixelStore;
		const page = this.pagePixelStore = new Map(this.pixelStore.map((name) => [name, Number(gl.getParameter(name))]));
		gl.pixelStorei = (name, value) =>
		{
			if (page.has(name))
			{
				page.set(name, Number(value));
			}
			setPixelStore(name, value);
		};
	}

	enter()
	{
		if (this.entered)
		{
			return;
		}
		const gl = this.gl;
		this.module.GL.makeContextCurrent(this.handle);
		// No queries: Chrome answers some pixel-store queries, and Firefox ACTIVE_TEXTURE, with a
		// round trip to the GPU process of about 0.2 to 0.7 ms. The page's values are known.
		const { pixelStore, pixelStoreDefaults, pagePixelStore, tracking } = this;
		for (let index = 0; index < pixelStore.length; index++)
		{
			if (pagePixelStore.get(pixelStore[index]) !== pixelStoreDefaults[index])
			{
				this.setPixelStore(pixelStore[index], pixelStoreDefaults[index]);
			}
		}
		// Emscripten sizes the core's uploads from its own copy of these two.
		this.module.GL.unpackAlignment = 4;
		this.module.GL.unpackRowLength = 0;
		tracking.pixelStore.clear();
		// The core's own GL calls can run before Filament's reset sets the active unit.
		gl.activeTexture(gl.TEXTURE0);
		tracking.unit = 0;
		gl.bindVertexArray(null);
		gl.bindBuffer(gl.ARRAY_BUFFER, null);
		gl.bindFramebuffer(gl.FRAMEBUFFER, null);
		gl.useProgram(null);
		this.entered = true;
		// Queued in Filament's command stream, so it runs before Filament's next GL call.
		for (const renderer of this.renderers)
		{
			try
			{
				renderer.resetGlState();
			}
			catch (error)
			{
				throw nativeError(this.module, error);
			}
		}
	}

	handBack()
	{
		if (!this.entered)
		{
			return;
		}
		const gl = this.gl;
		const { tracking, textureTargets } = this;
		// Only the units that the core bound. A texture that stays bound can form a feedback loop
		// with the page's framebuffer, or mismatch a sampler type of the page's programs, even
		// on units that the page does not sample.
		for (let index = 0; index < tracking.dirty.length; index++)
		{
			const unit = tracking.dirty[index];
			gl.activeTexture(gl.TEXTURE0 + unit);
			for (let bit = 0; bit < textureTargets.length; bit++)
			{
				if (tracking.targets[unit] & (1 << bit))
				{
					gl.bindTexture(textureTargets[bit], null);
				}
			}
			// The page sampling through Filament's sampler objects would read its textures with
			// Filament's filtering, and WebGL rejects draws whose samplers mismatch the texture.
			if (tracking.samplers[unit])
			{
				gl.bindSampler(unit, null);
			}
			tracking.targets[unit] = 0;
			tracking.samplers[unit] = 0;
		}
		tracking.dirty.length = 0;
		gl.activeTexture(gl.TEXTURE0);
		gl.bindVertexArray(null);
		for (let index = 0; index < this.bufferTargets.length; index++)
		{
			gl.bindBuffer(this.bufferTargets[index], null);
		}
		gl.bindFramebuffer(gl.FRAMEBUFFER, null);
		gl.bindRenderbuffer(gl.RENDERBUFFER, null);
		gl.useProgram(null);
		// GL defaults that renderers commonly assume instead of setting.
		gl.disable(gl.SCISSOR_TEST);
		gl.disable(gl.STENCIL_TEST);
		gl.disable(gl.RASTERIZER_DISCARD);
		gl.disable(gl.SAMPLE_ALPHA_TO_COVERAGE);
		gl.enable(gl.DITHER);
		gl.colorMask(true, true, true, true);
		gl.depthMask(true);
		gl.stencilMask(0xFFFFFFFF);
		// After enter, each value is the default unless the core set it.
		const { pixelStore, pixelStoreDefaults, pagePixelStore } = this;
		for (let index = 0; index < pixelStore.length; index++)
		{
			const name = pixelStore[index];
			const current = tracking.pixelStore.get(name) ?? pixelStoreDefaults[index];
			const page = pagePixelStore.get(name);
			if (current !== page)
			{
				this.setPixelStore(name, page);
			}
		}
		this.entered = false;
		for (const hook of this.onHandBack)
		{
			hook();
		}
	}

	call(fn)
	{
		this.enter();
		return callNative(this.module, fn);
	}

	// Whether every program that Filament created has finished linking. Filament reports a
	// program as compiled when its link starts, and its first draw then waits for the link; the
	// completion query of KHR_parallel_shader_compile does not wait. Without the extension,
	// Filament compiles synchronously and there is nothing to wait for.
	programsLinked()
	{
		const extension = this.parallelCompile ??= this.gl.getExtension("KHR_parallel_shader_compile") ?? false;
		if (!extension)
		{
			return true;
		}
		for (const program of this.module.GL.programs)
		{
			if (program && !this.gl.getProgramParameter(program, extension.COMPLETION_STATUS_KHR))
			{
				return false;
			}
		}
		return true;
	}

	// A WebGLTexture gets an Emscripten name, which Filament's GL calls use.
	textureName(texture)
	{
		const GL = this.module.GL;
		if (!texture.name || GL.textures[texture.name] !== texture)
		{
			const name = GL.getNewId(GL.textures);
			texture.name = name;
			GL.textures[name] = texture;
		}
		return texture.name;
	}
}

const contexts = new WeakMap();
// Native handles of wrappers that were garbage-collected without dispose().
const finalizer = new FinalizationRegistry((native) =>
{
	if (!native.isDeleted())
	{
		native.delete();
	}
});

class Handle
{
	constructor(context, native)
	{
		this._context = context;
		this._native = native;
		finalizer.register(this, native, this);
	}

	_call(method, ...args)
	{
		const context = this._context;
		context.enter();
		try
		{
			return this._native[method](...args);
		}
		catch (error)
		{
			throw nativeError(context.module, error);
		}
	}

	// For native calls that neither call GL nor queue GL work for later: they leave the
	// context with the page, so that the page can call them between hand-back and its draw.
	_get(method, ...args)
	{
		try
		{
			return this._native[method](...args);
		}
		catch (error)
		{
			throw nativeError(this._context.module, error);
		}
	}

	/** Whether two handles refer to the same filly object. */
	equals(other)
	{
		if (!(other instanceof this.constructor))
		{
			return false;
		}
		return typeof this._native.same === "function" ? this._get("same", other._native) : this === other;
	}

	/** Release the JavaScript handle. The filly object stays alive while others refer to it. */
	dispose()
	{
		finalizer.unregister(this);
		if (!this._native.isDeleted())
		{
			this._native.delete();
		}
	}
}

/**
 * Create a renderer that draws with the page's WebGL2 context.
 *
 * @param {WebGL2RenderingContext} gl
 * @param {Object} [options]
 * @param {Function} [options.onHandBack] - called after filly hands the context back, for the
 *   page renderer to forget its cached GL state (PIXI: () => pixiRenderer.reset()).
 */
export async function createRenderer(gl, { onHandBack = null } = {})
{
	const module = await loadModule();
	let context = contexts.get(gl);
	if (!context)
	{
		context = new Context(module, gl);
		contexts.set(gl, context);
	}
	if (onHandBack)
	{
		context.onHandBack.add(onHandBack);
	}
	context.enter();
	const native = callNative(module, () => new module.Renderer(context.handle));
	context.renderers.add(native);
	return new Renderer(context, native, onHandBack);
}

export class Renderer extends Handle
{
	constructor(context, native, onHandBack)
	{
		super(context, native);
		this._onHandBack = onHandBack;
	}

	createScene()
	{
		return new Scene(this._context, this._call("createScene"));
	}

	/**
	 * A camera that any scene of this renderer can use. It has a perspective projection (fovYDeg
	 * 30, near 0.1, far 100) that follows the render target, so frame() alone aims it.
	 */
	createCamera()
	{
		return new Camera(this._context, this._call("createCamera"));
	}

	/**
	 * Load a GLB or a glTF document with embedded resources as an Asset that any scene of this
	 * renderer can add(). Its loadWarnings lists features that load but render differently.
	 */
	loadAsset(bytes, { strict = false } = {})
	{
		const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
		const result = this._call("loadAssetBytes", data, strict);
		return loaded(Asset, this._context, result.asset, result.warnings);
	}

	/** loadAsset() for a glTF document and its external files: files maps relative URIs to bytes. */
	loadAssetFiles(main, files, { strict = false } = {})
	{
		return withFiles(this._context, files, (dir) =>
		{
			const result = this._call("loadAssetPath", `${dir}/${main}`, strict);
			return loaded(Asset, this._context, result.asset, result.warnings);
		});
	}

	/**
	 * Fetch and load a .glb, or a .gltf with the buffers and images it references, as an Asset.
	 * Its fetchedBytes is the number of bytes fetched.
	 */
	async loadAssetUrl(url, options = {})
	{
		const { bytes, main, files, size } = await fetchDocument(url);
		const asset = bytes ? this.loadAsset(bytes, options) : this.loadAssetFiles(main, files, options);
		asset.fetchedBytes = size;
		return asset;
	}

	/**
	 * An Environment from a 2:1 panorama of linear RGB float pixels, rows from the top. Loading
	 * filters it once; scenes that use it share the result.
	 */
	createEnvironment(pixels, width, height)
	{
		return new Environment(this._context, this._call("createEnvironment", pixels, width, height));
	}

	/** An Environment from a 2:1 panorama file: Radiance HDR, PNG, or JPEG bytes. */
	loadEnvironment(bytes)
	{
		return new Environment(this._context, withFiles(this._context, { environment: bytes }, (dir) =>
			this._call("loadEnvironmentPath", `${dir}/environment`)));
	}

	/** An Environment from Filament's cmgen: the IBL KTX bytes and optionally the skybox KTX bytes. */
	loadEnvironmentKtx(ibl, skybox = null)
	{
		const files = skybox ? { "ibl.ktx": ibl, "skybox.ktx": skybox } : { "ibl.ktx": ibl };
		return new Environment(this._context, withFiles(this._context, files, (dir) =>
			this._call("loadEnvironmentKtxPath", `${dir}/ibl.ktx`, skybox ? `${dir}/skybox.ktx` : "")));
	}

	/**
	 * Render into a texture that the page owns. It must be a TEXTURE_2D with RGBA8 storage of
	 * the given size; WebGL2 cannot check this.
	 */
	importTarget(texture, width, height, { depth = true } = {})
	{
		const name = this._context.textureName(texture);
		return new ImportedTarget(this._context, this._call("importGlTarget", name, width, height, depth), texture);
	}

	/** A Filament-owned target whose pixels read() returns. */
	createRenderTarget(width, height, { depth = true } = {})
	{
		return new RenderTarget(this._context, this._call("createRenderTarget", width, height, depth));
	}

	/**
	 * Render one frame into an ImportedTarget or a RenderTarget. options: camera, viewport
	 * [x, y, width, height] from the lower left, clear.
	 */
	render(scene, target, { camera = null, viewport = null, clear = true } = {})
	{
		const method = target instanceof RenderTarget ? "renderOffscreen" : "render";
		this._call(method, scene._native, target._native, camera ? camera._native : null, viewport, clear);
	}

	/**
	 * A texture that materials sample, from pixels: a Uint8Array, or a Float32Array of linear
	 * values, with rows from the top and `channels` (1, 3, or 4) values per pixel. colorSpace is
	 * "srgb" or "linear" ("linear" for float pixels).
	 */
	createTexture(pixels, { width, height, channels = 4, colorSpace, mipmaps = false, filter = "linear", wrap = "repeat" })
	{
		return new Texture(this._context,
			this._call("createTexture", pixels, width, height, channels, colorSpace, mipmaps, filter, wrap));
	}

	/**
	 * A texture of the page that materials sample. For colorSpace "srgb", the page must allocate
	 * it with SRGB8_ALPHA8 storage; for "linear", with RGBA8 storage.
	 */
	importTexture(texture, width, height, { colorSpace, filter = "linear", wrap = "repeat" })
	{
		const name = this._context.textureName(texture);
		return new ImportedTexture(this._context, this._call("importGlTexture", name, width, height, colorSpace, filter, wrap));
	}

	/**
	 * Compile the GPU programs that rendering this scene needs, for its current models and
	 * settings, without blocking the page: the browser compiles them in the background
	 * (KHR_parallel_shader_compile), and this polls between turns of the event loop. Without
	 * it, the first render blocks while it compiles. Resolves when all are compiled.
	 */
	async prepare(scene, { timeoutMs = 60000, pollMs = 8 } = {})
	{
		const preparation = this._call("prepare", scene._native);
		try
		{
			const start = performance.now();
			while (!this._context.call(() => preparation.ready()) || !this._context.programsLinked())
			{
				if (performance.now() - start > timeoutMs)
				{
					throw new FillyError(`Shader compilation did not finish in ${timeoutMs} ms`);
				}
				// Hand the context back, so that the page can draw while the compiler works.
				this._context.handBack();
				await new Promise((resolve) => setTimeout(resolve, pollMs));
			}
		}
		finally
		{
			preparation.delete();
		}
	}

	/**
	 * Ask what the scene shows at target pixel (x, y), counted from the lower-left corner like
	 * viewports. The scene's next render() takes the query; see PickQuery.
	 */
	pick(scene, x, y)
	{
		return new PickQuery(this._context, this._call("pick", scene._native, x, y));
	}

	/** Give the context back to the page; call it before the page draws. */
	handBack()
	{
		this._context.handBack();
	}

	finish()
	{
		this._call("finish");
	}

	/** Frame counters, CPU timings, and GPU frame time, as Renderer.stats in Python. */
	get stats()
	{
		// 64-bit counters arrive as BigInt; their values fit a Number. Null stays null.
		const stats = this._get("stats");
		for (const key of Object.keys(stats))
		{
			if (stats[key] !== null)
			{
				stats[key] = Number(stats[key]);
			}
		}
		return stats;
	}

	get glPlatform()
	{
		return this._get("glPlatform");
	}

	get closed()
	{
		return this._get("closed");
	}

	close()
	{
		this._call("close");
		this._context.renderers.delete(this._native);
		if (this._onHandBack)
		{
			this._context.onHandBack.delete(this._onHandBack);
		}
		this._context.handBack();
	}
}

let fileCounter = 0;
const GLB_MAGIC = 0x46546C67;

// Runs fn with the files in the module's file system, under a folder that fn receives. The core
// reads glTF resources and environment files from there.
function withFiles(context, files, fn)
{
	const FS = context.module.FS;
	const dir = `/filly-files-${++fileCounter}`;
	const written = [];
	try
	{
		for (const [name, data] of Object.entries(files))
		{
			const path = `${dir}/${name}`;
			FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
			// canOwn: the file uses the caller's bytes instead of a copy. Nothing writes to the
			// file, and it is removed before this returns.
			FS.writeFile(path, data instanceof Uint8Array ? data : new Uint8Array(data), { canOwn: true });
			written.push(path);
		}
		return fn(dir);
	}
	finally
	{
		for (const path of written)
		{
			FS.unlink(path);
		}
	}
}

async function fetchBytes(url)
{
	const response = await fetch(url);
	if (!response.ok)
	{
		throw new AssetError(`Could not fetch ${url}: ${response.status} ${response.statusText}`);
	}
	return new Uint8Array(await response.arrayBuffer());
}

// Fetches a .glb, or a .gltf with the buffers and images that it references: { bytes } for a GLB
// or { main, files } for a glTF document, and size, the number of bytes fetched.
async function fetchDocument(url)
{
	const base = new URL(url, document.baseURI);
	const bytes = await fetchBytes(base);
	// The content decides, not the URL, which can lack an extension or carry a query.
	if (bytes.length >= 4 && new DataView(bytes.buffer, bytes.byteOffset, 4).getUint32(0, true) === GLB_MAGIC)
	{
		return { bytes, size: bytes.length };
	}
	let gltf;
	try
	{
		gltf = JSON.parse(new TextDecoder().decode(bytes));
	}
	catch
	{
		throw new AssetError(`${base.href} is neither a GLB file nor a glTF document`);
	}
	const uris = [...(gltf.buffers ?? []), ...(gltf.images ?? [])]
		.map((item) => item.uri).filter((uri) => uri && !uri.startsWith("data:"));
	const main = "model.gltf";
	const files = { [main]: bytes };
	let size = bytes.length;
	await Promise.all(uris.map(async (uri) =>
	{
		const file = await fetchBytes(new URL(uri, base));
		files[decodeURIComponent(uri)] = file;
		size += file.length;
	}));
	return { main, files, size };
}

// Wraps a native load result, { model } or { asset } with warnings, and reports the warnings.
function loaded(Type, context, native, warnings)
{
	const result = new Type(context, native);
	result.loadWarnings = warnings;
	for (const warning of warnings)
	{
		console.warn(`filly: ${warning}`);
	}
	return result;
}

export class Scene extends Handle
{
	get camera()
	{
		return new Camera(this._context, this._get("camera"));
	}

	set camera(camera)
	{
		this._call("setCamera", camera._native);
	}

	/**
	 * Load a GLB or a glTF document with embedded resources and add it to this scene, as
	 * renderer.loadAsset() and add() do, without keeping the asset. Returns a Model; its
	 * loadWarnings lists features that load but render differently.
	 */
	load(bytes, { strict = false } = {})
	{
		const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
		const result = this._call("loadBytes", data, strict);
		return loaded(Model, this._context, result.model, result.warnings);
	}

	/** Load a glTF document and its external files: files maps relative URIs to bytes. */
	loadFiles(main, files, { strict = false } = {})
	{
		return withFiles(this._context, files, (dir) =>
		{
			const result = this._call("loadPath", `${dir}/${main}`, strict);
			return loaded(Model, this._context, result.model, result.warnings);
		});
	}

	/**
	 * Fetch and load a .glb, or a .gltf with the buffers and images it references. The model's
	 * fetchedBytes is the number of bytes fetched.
	 */
	async loadUrl(url, options = {})
	{
		const { bytes, main, files, size } = await fetchDocument(url);
		const model = bytes ? this.load(bytes, options) : this.loadFiles(main, files, options);
		model.fetchedBytes = size;
		return model;
	}

	/** A new Model of an Asset from renderer.loadAsset(). */
	add(asset)
	{
		return new Model(this._context, this._call("add", asset._native));
	}

	/**
	 * A model from vertex arrays (see shapes): positions and indices are required; normals,
	 * uvs, and colors (4 per vertex, linear) are optional. Material options: baseColor,
	 * metallic (default 0), roughness (default 1), emissive, unlit, doubleSided, and alphaMode
	 * ("opaque", "mask", or "blend").
	 */
	createMesh(options)
	{
		return new Model(this._context, this._call("createMesh", options));
	}

	addDirectionalLight({ direction, intensity = 50000, color = [1, 1, 1] })
	{
		return new Light(this._context, this._call("addDirectionalLight", direction, intensity, color));
	}

	addSunLight({ direction, intensity = 100000, color = [1, 1, 1], angularRadiusDeg = 0.545, haloSize = 10, haloFalloff = 80 })
	{
		return new Light(this._context,
			this._call("addSunLight", direction, intensity, color, angularRadiusDeg, haloSize, haloFalloff));
	}

	/** intensity in candela: 50000 lights a surface 1 m away as the default directional light does. */
	addPointLight({ position, intensity = 50000, color = [1, 1, 1], range = 10 })
	{
		return new Light(this._context, this._call("addPointLight", position, intensity, color, range));
	}

	/** coneDeg: [inner, outer] half-angles in degrees. */
	addSpotLight({ position, direction, intensity = 50000, color = [1, 1, 1], range = 10, coneDeg = [15, 30] })
	{
		return new Light(this._context,
			this._call("addSpotLight", position, direction, intensity, color, range, coneDeg));
	}

	/** The Environment that lights the scene, or null. */
	get environment()
	{
		const native = this._get("environment");
		return native ? new Environment(this._context, native) : null;
	}

	set environment(environment)
	{
		this._call("setEnvironment", environment ? environment._native : null);
	}

	close()
	{
		this._call("close");
	}

	get closed()
	{
		return this._get("closed");
	}
}

// Scene options with a getter and a setter of the same name in the bindings. Effect parameters
// keep their values while the effect is off. environmentIntensity (lux), environmentRotationDeg,
// and environmentVisible apply to whichever environment the scene uses, and keep their values
// without one.
for (const name of ["encoding", "outputPath", "toneMapping", "antialiasing", "msaa", "shadows",
	"transparent", "dithering", "ssao", "bloom", "fog", "fogColor", "fogDensity", "fogStart", "depthOfField",
	"vignette", "vignetteMidpoint", "vignetteRoundness", "vignetteFeather", "vignetteColor", "background",
	"environmentIntensity", "environmentVisible", "environmentRotationDeg", "pickBlended"])
{
	defineProperty(Scene, name);
}

function defineProperty(cls, name)
{
	const setter = `set${name[0].toUpperCase()}${name.slice(1)}`;
	Object.defineProperty(cls.prototype, name, {
		get() { return this._get(name); },
		set(value) { this._call(setter, value); },
	});
}

/**
 * A glTF asset that the renderer keeps, so that any of its scenes can add() new models of it.
 * close() releases the source data that add() reads; models that exist keep the GPU data.
 */
export class Asset extends Handle
{
	/**
	 * Memory that the asset holds, shared with its models: { gpuTextureBytes, gpuGeometryBytes,
	 * gpuBytes, cpuBytes, instanceGpuBytes, instanceCpuBytes, models }. See Asset.memory in
	 * docs/reference/api.md. The CPU part lives in the WebAssembly heap, which never shrinks:
	 * freed memory is reused, but the page keeps its high-water mark.
	 */
	get memory()
	{
		return this._get("memory");
	}

	close()
	{
		this._call("close");
	}

	get closed()
	{
		return this._get("closed");
	}
}

/**
 * Image-based lighting that any scene of the renderer can use (scene.environment). close()
 * removes it from the scenes that use it.
 */
export class Environment extends Handle
{
	close()
	{
		this._call("close");
	}

	get closed()
	{
		return this._get("closed");
	}
}

export class Camera extends Handle
{
	/** Scenes that use the camera lose it. Imported cameras close with their model. */
	close()
	{
		this._call("close");
	}

	get closed()
	{
		return this._get("closed");
	}

	/**
	 * fovYDeg is the vertical field of view in degrees. aspect 0 follows the render target. near
	 * and far are optional: a plane that is given stays until the next projection call, and
	 * frame() sets the others.
	 */
	setPerspective({ fovYDeg, aspect = 0, near, far })
	{
		this._call("setPerspective", fovYDeg, aspect, near, far);
	}

	setLensProjection({ focalLength, aspect = 0, near, far })
	{
		this._call("setLensProjection", focalLength, aspect, near, far);
	}

	/** Either { left, right, bottom, top, near, far } or { height, center, near, far }. */
	setOrthographic({ left, right, bottom, top, height, center = [0, 0], near, far })
	{
		if (height !== undefined)
		{
			this._call("setOrthographicHeight", height, center[0], center[1], near, far);
		}
		else
		{
			this._call("setOrthographic", left, right, bottom, top, near, far);
		}
	}

	lookAt(target, up = [0, 1, 0])
	{
		this._call("lookAt", target, up);
	}

	/**
	 * Aim at a Model, a Node, or a [min, max] box and move to fill part of the view. Returns the
	 * distance. Without options.aspect, render() frames again for a target of another aspect.
	 */
	frame(target, options = {})
	{
		if (target instanceof Model)
		{
			return this._call("frameModel", target._native, options);
		}
		if (target instanceof Node)
		{
			return this._call("frameNode", target._native, options);
		}
		return this._call("frameBox", target, options);
	}

	/** The glTF node of an imported camera; null for a camera from renderer.createCamera(). */
	get node()
	{
		return this._get("hasNode") ? new Node(this._context, this._get("node")) : null;
	}

	get viewMatrix()
	{
		return this._get("viewMatrix");
	}

	get projection()
	{
		return this._get("projection");
	}
}
for (const name of ["position", "transform", "exposure", "focusDistance", "aperture"])
{
	defineProperty(Camera, name);
}

export class Model extends Handle
{
	get bounds()
	{
		return this._get("bounds");
	}

	root()
	{
		return new Node(this._context, this._get("root"));
	}

	node(key)
	{
		return new Node(this._context, typeof key === "number" ? this._get("nodeByIndex", key) : this._get("nodeByName", key));
	}

	get nodeNames()
	{
		return this._get("nodeNames");
	}

	/** All nodes in glTF order. */
	get nodes()
	{
		return this._get("nodes").map((node) => new Node(this._context, node));
	}

	/** The memory of the model's asset, shared with the asset's other models; see Asset.memory. */
	get memory()
	{
		return this._get("memory");
	}

	/** Whether the model is a generated mesh, which updateMesh() can change. */
	get isMesh()
	{
		return this._get("isMesh");
	}

	get vertexCount()
	{
		return this._get("vertexCount");
	}

	/** Replace vertex data of a generated mesh: positions, normals, uvs, or colors, as in createMesh. */
	updateMesh(arrays)
	{
		this._call("updateMesh", arrays);
	}

	material(name)
	{
		return new Material(this._context, this._get("material", name));
	}

	get materialNames()
	{
		return this._get("materialNames");
	}

	/** [{ name, duration }] in glTF order. */
	get animations()
	{
		return this._get("animations");
	}

	applyAnimation(key, time, { loop = true } = {})
	{
		this._call(typeof key === "number" ? "applyAnimationByIndex" : "applyAnimationByName", key, time, loop);
	}

	resetAnimation()
	{
		this._call("resetAnimation");
	}

	get variants()
	{
		return this._get("variants");
	}

	applyVariant(key)
	{
		this._call(typeof key === "number" ? "applyVariantByIndex" : "applyVariantByName", key);
	}

	/** The model's open lights. Closed lights are left out; light(key) still returns them, closed. */
	get lights()
	{
		return this._get("lights").map((light) => new Light(this._context, light));
	}

	/** The light on the node with this name or glTF index. */
	light(key)
	{
		return new Light(this._context, this._get(typeof key === "number" ? "lightByIndex" : "lightByName", key));
	}

	camera(key)
	{
		return new Camera(this._context, this._get(typeof key === "number" ? "cameraByIndex" : "cameraByName", key));
	}

	get cameras()
	{
		return this._get("cameras").map((camera) => new Camera(this._context, camera));
	}

	close()
	{
		this._call("close");
	}

	get closed()
	{
		return this._get("closed");
	}
}
for (const name of ["transform", "position", "visible"])
{
	defineProperty(Model, name);
}
// Rotation and scale apply to the model's root node, as in the Python API.
for (const name of ["scale", "quaternion", "rotationEulerDeg"])
{
	defineProperty(Model, name);
}

export class Node extends Handle
{
	get name()
	{
		return this._get("name");
	}

	get index()
	{
		return this._get("index");
	}

	get meshName()
	{
		return this._get("meshName");
	}

	/** The parent node; null for a top-level node. */
	get parent()
	{
		const parent = this._get("parent");
		return parent ? new Node(this._context, parent) : null;
	}

	get children()
	{
		return this._get("children").map((node) => new Node(this._context, node));
	}

	get morphTargetCount()
	{
		return this._get("morphTargetCount");
	}

	setMorphWeights(weights)
	{
		this._call("setMorphWeights", weights);
	}

	material(slot = 0)
	{
		// Not _get: the first access duplicates the node's material instance, an engine command.
		return new Material(this._context, this._call("material", slot));
	}

	get bounds()
	{
		return this._get("bounds");
	}
}
for (const name of ["transform", "position", "scale", "quaternion", "rotationEulerDeg"])
{
	defineProperty(Node, name);
}

const TEXTURE_SLOTS = { baseColor: 0, emissive: 1 };

export class Material extends Handle
{
	_texture(slot)
	{
		const native = this._get("texture", slot);
		if (!native)
		{
			return null;
		}
		return native instanceof this._context.module.ImportedTexture ? new ImportedTexture(this._context, native)
			: new Texture(this._context, native);
	}

	_setTexture(slot, texture)
	{
		if (texture === null || texture === undefined)
		{
			this._call("clearTexture", slot);
		}
		else if (texture instanceof ImportedTexture)
		{
			this._call("setImportedTexture", slot, texture._native);
		}
		else if (texture instanceof Texture)
		{
			this._call("setTexture", slot, texture._native);
		}
		else
		{
			throw new TypeError("Texture slots take a Texture, an ImportedTexture, or null");
		}
	}

	/** A Texture, an ImportedTexture, or null. */
	get baseColorTexture()
	{
		return this._texture(0);
	}

	set baseColorTexture(texture)
	{
		this._setTexture(0, texture);
	}

	get emissiveTexture()
	{
		return this._texture(1);
	}

	set emissiveTexture(texture)
	{
		this._setTexture(1, texture);
	}

	// One component of a slot's KHR_texture_transform; null without a texture in the slot.
	_uv(slot, key)
	{
		const transform = this._get("textureTransform", slot);
		return transform ? transform[key] : null;
	}

	_setUv(slot, key, value)
	{
		const transform = this._get("textureTransform", slot) ?? { offset: [0, 0], scale: [1, 1], rotationDeg: 0 };
		transform[key] = value;
		this._call("setTextureTransform", slot, transform.offset, transform.scale, transform.rotationDeg);
	}
}
for (const name of ["baseColor", "metallic", "roughness", "emissive"])
{
	defineProperty(Material, name);
}
// baseColorTextureOffset, baseColorTextureScale, baseColorTextureRotationDeg, and the same for
// emissive: offset and scale in UV units, rotation in degrees.
for (const [prefix, slot] of Object.entries(TEXTURE_SLOTS))
{
	for (const [suffix, key] of [["Offset", "offset"], ["Scale", "scale"], ["RotationDeg", "rotationDeg"]])
	{
		Object.defineProperty(Material.prototype, `${prefix}Texture${suffix}`, {
			get() { return this._uv(slot, key); },
			set(value) { this._setUv(slot, key, value); },
		});
	}
}

export class Light extends Handle
{
	get type()
	{
		return this._get("type");
	}

	/** The glTF node that places an imported light; null for a light that a scene created. */
	get node()
	{
		return this._get("hasNode") ? new Node(this._context, this._get("node")) : null;
	}

	close()
	{
		this._call("close");
	}

	get closed()
	{
		return this._get("closed");
	}
}
// A property that the light type lacks reads as null, and setting it throws: position and range
// of directional and sun lights, direction of point lights, coneDeg of all but spot lights.
for (const name of ["color", "intensity", "position", "direction", "range", "coneDeg", "castsShadows",
	"shadowMapSize", "shadowConstantBias", "shadowNormalBias"])
{
	defineProperty(Light, name);
}

/** A page-owned texture that filly renders into. The page samples it after handBack(). */
export class ImportedTarget extends Handle
{
	constructor(context, native, texture)
	{
		super(context, native);
		this.texture = texture;
	}

	get width()
	{
		return this._get("width");
	}

	get height()
	{
		return this._get("height");
	}

	/**
	 * Read the texture: RGBA bytes, rows from the top, as RenderTarget.read() in Python. It
	 * waits for the GPU, so use it for tests and screenshots, not in a frame loop.
	 */
	read()
	{
		const context = this._context;
		const gl = context.gl;
		context.handBack();
		const width = this.width;
		const height = this.height;
		const previous = gl.getParameter(gl.FRAMEBUFFER_BINDING);
		const framebuffer = gl.createFramebuffer();
		const pixels = new Uint8Array(width * height * 4);
		try
		{
			gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
			gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.texture, 0);
			gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
		}
		finally
		{
			gl.bindFramebuffer(gl.FRAMEBUFFER, previous);
			gl.deleteFramebuffer(framebuffer);
		}
		// GL rows start at the bottom.
		const rows = new Uint8Array(pixels.length);
		const stride = width * 4;
		for (let row = 0; row < height; row++)
		{
			rows.set(pixels.subarray(row * stride, (row + 1) * stride), (height - 1 - row) * stride);
		}
		return rows;
	}

	close()
	{
		this._call("close");
	}

	get closed()
	{
		return this._get("closed");
	}
}

/**
 * A pick that Renderer.pick() queued. WebGL completes the GPU readback only after control
 * returns to the browser, so the answer arrives on a later turn of the event loop: poll ready()
 * once per frame, or await wait().
 */
export class PickQuery extends Handle
{
	/** Whether the answer arrived. Does not wait for the GPU. */
	ready()
	{
		return this._call("ready");
	}

	/** framesRendered just after the render that took the query, or null before it. */
	get frameNumber()
	{
		return this._get("frameNumber");
	}

	/**
	 * { model, node, position, depth } of the model surface at the point, or null for none:
	 * position in world space, depth along the camera's viewing direction. Throws until ready().
	 */
	get result()
	{
		const result = this._call("result");
		return result && { model: new Model(this._context, result.model), node: new Node(this._context, result.node),
			position: result.position, depth: result.depth };
	}

	/** Resolves to result when the answer arrives. A render of the scene must take the query first. */
	async wait({ timeoutMs = 10000 } = {})
	{
		const start = performance.now();
		while (!this.ready())
		{
			if (performance.now() - start > timeoutMs)
			{
				throw new FillyError("The pick did not complete; render the scene to take it");
			}
			this._context.handBack();
			await new Promise((resolve) => setTimeout(resolve, 0));
		}
		return this.result;
	}
}

/** A Filament-owned color target. */
export class RenderTarget extends Handle
{
	get width()
	{
		return this._get("width");
	}

	get height()
	{
		return this._get("height");
	}

	/**
	 * RGBA bytes, rows from the top. WebGL completes a readback only after control returns to
	 * the browser, so this resolves on a later turn of the event loop.
	 */
	async read({ timeoutMs = 10000 } = {})
	{
		const readback = this._call("beginRead");
		try
		{
			const start = performance.now();
			while (!this._context.call(() => readback.ready()))
			{
				if (performance.now() - start > timeoutMs)
				{
					throw new FillyError("The GPU readback did not complete");
				}
				await new Promise((resolve) => setTimeout(resolve, 0));
			}
			return this._context.call(() => readback.take());
		}
		finally
		{
			readback.delete();
		}
	}

	close()
	{
		this._call("close");
	}

	get closed()
	{
		return this._get("closed");
	}
}

/** A texture with pixels from JavaScript. update() replaces them. */
export class Texture extends Handle
{
	get width()
	{
		return this._get("width");
	}

	get height()
	{
		return this._get("height");
	}

	get channels()
	{
		return this._get("channels");
	}

	/** "uint8" or "float32". */
	get dtype()
	{
		return this._get("isFloat") ? "float32" : "uint8";
	}

	get colorSpace()
	{
		return this._get("colorSpace");
	}

	get mipmaps()
	{
		return this._get("mipmaps");
	}

	/** New pixels of the same size and type. */
	update(pixels)
	{
		this._call("update", pixels);
	}

	close()
	{
		this._call("close");
	}

	get closed()
	{
		return this._get("closed");
	}
}

/**
 * A texture of the page that materials sample. The page writes it between beginWrite() and
 * endWrite(), or in write(fn). WebGL keeps the order of the page's writes and filly's reads.
 */
export class ImportedTexture extends Handle
{
	get width()
	{
		return this._get("width");
	}

	get height()
	{
		return this._get("height");
	}

	get colorSpace()
	{
		return this._get("colorSpace");
	}

	beginWrite()
	{
		this._call("beginWrite");
	}

	endWrite()
	{
		this._call("endWrite");
	}

	/** Run fn between beginWrite() and endWrite(), with the context handed back to the page. */
	write(fn)
	{
		this.beginWrite();
		try
		{
			this._context.handBack();
			return fn();
		}
		finally
		{
			this.endWrite();
		}
	}

	get writing()
	{
		return this._get("writing");
	}

	close()
	{
		this._call("close");
	}

	get closed()
	{
		return this._get("closed");
	}
}
