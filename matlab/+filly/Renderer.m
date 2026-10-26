classdef Renderer < filly.Object
    % RENDERER  A Filament engine: offscreen, or sharing a host OpenGL context.
    %
    %   r = filly.Renderer() renders offscreen into filly.RenderTarget objects.
    %   r = filly.Renderer('SharedContext', ctx) shares the host OpenGL context ctx, which must
    %   be current. For Psychtoolbox, use filly.Renderer.forWindow(win).
    %
    %   Scenes, cameras, assets, environments, textures, and targets belong to the renderer.
    %   delete(r) or close(r) closes the renderer and everything that it owns; a closed object
    %   raises a 'filly:error' error when used. Close the renderer before the window that it
    %   shares; after Psychtoolbox's sca, close(r) still releases everything.

    properties (Dependent, SetAccess = private)
        closed
        sharedContext
        glPlatform
        stats
    end

    properties (Hidden)
        % One row [window ptbTexture glTexture] per open filly.PTBTarget or filly.PTBTexture. Psychtoolbox owns
        % these textures; close() releases them after Filament no longer uses them.
        HostTextures = zeros(0, 3)
    end

    methods
        function obj = Renderer(varargin)
            o = filly.internal.options(struct('SharedContext', []), varargin);
            obj.Id = filly_mex('Renderer.new', o.SharedContext);
        end

        function delete(obj)
            % Octave 11 can call this again after Object.delete released the handle; the
            % renderer was closed on the first call.
            if obj.Id ~= 0 && filly_mex('valid', obj.Id)
                try
                    close(obj);
                catch err
                    warning('filly:closeFailed', 'Renderer close failed: %s', err.message);
                end
            end
        end

        function close(obj)
            % Close the renderer and every object that it owns. Repeated calls do nothing.
            hosts = obj.HostTextures;
            if ~isempty(hosts)
                % With a Psychtoolbox window open, its context must be current, so that the
                % core can fence the host's last draws before it releases the textures.
                filly.internal.ptbActivateAny(hosts(:, 1));
            end
            try
                filly_mex('Renderer.close', obj.Id);
            catch err
                obj.HostTextures = zeros(0, 3);
                filly.internal.ptbRelease(hosts);
                rethrow(err);
            end
            obj.HostTextures = zeros(0, 3);
            filly.internal.ptbRelease(hosts);
        end

        function scene = createScene(obj)
            scene = filly.Scene(obj, filly_mex('Renderer.createScene', obj.Id));
        end

        function camera = createCamera(obj)
            % A camera with a 30 degree vertical field of view, near 0.1, far 100, whose
            % aspect follows the render target.
            camera = filly.Camera(obj, filly_mex('Renderer.createCamera', obj.Id));
        end

        function asset = loadAsset(obj, source, varargin)
            % Load a glTF or GLB file (char path) or GLB bytes (uint8) for scene.add().
            % Option 'Strict' (false): raise filly:asset for features that would render
            % differently, instead of a filly:assetCompatibility warning.
            o = filly.internal.options(struct('Strict', false), varargin);
            if ~isa(source, 'uint8'), source = filly.internal.text(source); end
            [id, warnings] = filly_mex('Renderer.loadAsset', obj.Id, source, o.Strict);
            asset = filly.Asset(obj, id);
            filly.Renderer.warn(warnings);
        end

        function environment = loadEnvironment(obj, path)
            % Image-based lighting from a 2:1 panorama: Radiance HDR, PNG, or JPEG.
            environment = filly.Environment(obj, filly_mex('Renderer.loadEnvironment', obj.Id, filly.internal.text(path)));
        end

        function environment = loadEnvironmentKtx(obj, iblPath, skyboxPath)
            % Prefiltered cubemaps from cmgen. Without a skybox, the sharpest IBL level shows.
            if nargin < 3, skyboxPath = ''; end
            environment = filly.Environment(obj, filly_mex('Renderer.loadEnvironmentKtx', obj.Id, ...
                filly.internal.text(iblPath), filly.internal.text(skyboxPath)));
        end

        function environment = createEnvironment(obj, pixels)
            % Image-based lighting from an H x 2H x 3 array of linear RGB values.
            environment = filly.Environment(obj, filly_mex('Renderer.createEnvironment', obj.Id, pixels));
        end

        function target = createRenderTarget(obj, width, height, varargin)
            % An offscreen target that read() copies to MATLAB. Options: 'Format' ('rgba8'),
            % 'Depth' (true).
            o = filly.internal.options(struct('Format', 'rgba8', 'Depth', true), varargin);
            target = filly.RenderTarget(obj, filly_mex('Renderer.createRenderTarget', obj.Id, width, height, ...
                filly.internal.text(o.Format), o.Depth));
        end

        function target = importGlTarget(obj, texture, width, height, varargin)
            % A host GL_TEXTURE_2D with GL_RGBA8 storage that the renderer draws into. For
            % Psychtoolbox windows, use filly.PTBTarget.
            o = filly.internal.options(struct('Format', 'rgba8', 'Depth', true), varargin);
            target = filly.ImportedTarget(obj, filly_mex('Renderer.importGlTarget', obj.Id, texture, width, height, ...
                filly.internal.text(o.Format), o.Depth));
        end

        function render(obj, scene, target, varargin)
            % Render the scene into a RenderTarget, ImportedTarget, or PTBTarget.
            % Options: 'Camera' (the scene's camera), 'Viewport' ([x y width height] from the
            % lower-left corner, in pixels), 'Clear' (true).
            if isempty(varargin)
                filly_mex('Renderer.render', obj.Id, scene.Id, target.Id);
                return
            end
            o = filly.internal.options(struct('Camera', [], 'Viewport', [], 'Clear', true), varargin);
            camera = [];
            if ~isempty(o.Camera), camera = o.Camera.Id; end
            filly_mex('Renderer.render', obj.Id, scene.Id, target.Id, camera, o.Viewport, o.Clear);
        end

        function query = pick(obj, scene, x, y)
            % Queue a query for what the scene shows at target pixel (x, y), from the
            % lower-left corner. The scene's next render answers it.
            query = filly.PickQuery(obj, filly_mex('Renderer.pick', obj.Id, scene.Id, x, y));
        end

        function texture = createTexture(obj, pixels, varargin)
            % A texture from an H x W or H x W x C (C = 1, 3, 4) uint8 or single array, first
            % row at the top. Options: 'ColorSpace' ('srgb' or 'linear', required),
            % 'Mipmaps' (false), 'Filter' ('linear'), 'Wrap' ('repeat').
            o = filly.internal.options(struct('ColorSpace', '', 'Mipmaps', false, 'Filter', 'linear', ...
                'Wrap', 'repeat'), varargin);
            if isempty(o.ColorSpace)
                error('filly:invalidArgument', 'createTexture needs ''ColorSpace'', ''srgb'' or ''linear''');
            end
            texture = filly.Texture(obj, filly_mex('Renderer.createTexture', obj.Id, pixels, ...
                filly.internal.text(o.ColorSpace), o.Mipmaps, filly.internal.text(o.Filter), filly.internal.text(o.Wrap)));
        end

        function texture = importGlTexture(obj, texture, width, height, varargin)
            % A host GL_TEXTURE_2D with GL_RGBA8 storage that materials sample. Options:
            % 'ColorSpace' (required), 'Filter' ('linear'), 'Wrap' ('repeat').
            o = filly.internal.options(struct('ColorSpace', '', 'Filter', 'linear', 'Wrap', 'repeat'), varargin);
            if isempty(o.ColorSpace)
                error('filly:invalidArgument', 'importGlTexture needs ''ColorSpace'', ''srgb'' or ''linear''');
            end
            texture = filly.ImportedTexture(obj, filly_mex('Renderer.importGlTexture', obj.Id, texture, width, ...
                height, filly.internal.text(o.ColorSpace), filly.internal.text(o.Filter), filly.internal.text(o.Wrap)));
        end

        function preparation = prepare(obj, scene)
            % Start compiling the GPU programs that rendering the scene needs.
            preparation = filly.Preparation(obj, filly_mex('Renderer.prepare', obj.Id, scene.Id));
        end

        function finish(obj)
            % Wait until the GPU has finished all submitted work.
            filly_mex('Renderer.finish', obj.Id);
        end

        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
        function v = get.sharedContext(obj), v = filly_mex('get', obj.Id, 'sharedContext'); end
        function v = get.glPlatform(obj), v = filly_mex('get', obj.Id, 'glPlatform'); end
        function v = get.stats(obj), v = filly_mex('get', obj.Id, 'stats'); end
    end

    methods (Static)
        function obj = forWindow(window)
            % A renderer that shares the OpenGL context of a Psychtoolbox onscreen window.
            filly.internal.ptbActivate(window);
            obj = filly.Renderer('SharedContext', filly_mex('currentContext'));
        end
    end

    methods (Static, Hidden)
        function warn(messages)
            for i = 1:numel(messages)
                warning('filly:assetCompatibility', '%s', messages{i});
            end
        end
    end
end
