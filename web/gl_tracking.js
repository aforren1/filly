// Emscripten JavaScript library, linked with --js-library: replaces some of Emscripten's GL
// functions, which only the core's calls go through, never the page's.
//
// Hand-back must undo the texture, sampler, and pixel-store state that the core leaves. Sweeping
// every texture unit costs six WebGL calls per unit, for 32 to 192 units, in each frame. These
// versions record what the core changed in the `filly` record of the current context, which
// web/filly.js creates, so that hand-back undoes only that.

addToLibrary({
  glActiveTexture: (texture) => {
    var filly = GL.currentContext.filly;
    if (filly) filly.unit = texture - 0x84C0 /* GL_TEXTURE0 */;
    GLctx.activeTexture(texture);
  },

  glBindTexture: (target, texture) => {
    var filly = GL.currentContext.filly;
    if (filly && texture) {
      var unit = filly.unit;
      // Bits as in filly.js TEXTURE_TARGETS.
      var bit = target == 0x0DE1 /* GL_TEXTURE_2D */ ? 1 : target == 0x8513 /* GL_TEXTURE_CUBE_MAP */ ? 2
        : target == 0x8C1A /* GL_TEXTURE_2D_ARRAY */ ? 4 : 8;
      if (!filly.targets[unit] && !filly.samplers[unit]) filly.dirty.push(unit);
      filly.targets[unit] |= bit;
    }
    GLctx.bindTexture(target, GL.textures[texture]);
  },

  glBindSampler: (unit, sampler) => {
    var filly = GL.currentContext.filly;
    if (filly && sampler) {
      if (!filly.targets[unit] && !filly.samplers[unit]) filly.dirty.push(unit);
      filly.samplers[unit] = 1;
    }
    GLctx.bindSampler(unit, GL.samplers[sampler]);
  },

  glPixelStorei: (pname, param) => {
    if (pname == 0x0CF5 /* GL_UNPACK_ALIGNMENT */) {
      GL.unpackAlignment = param;
    } else if (pname == 0x0CF2 /* GL_UNPACK_ROW_LENGTH */) {
      GL.unpackRowLength = param;
    }
    var filly = GL.currentContext.filly;
    if (filly) {
      filly.pixelStore.set(pname, Number(param));
      // Past filly.js's wrapper, which records only the page's sets.
      filly.setPixelStore(pname, param);
    } else {
      GLctx.pixelStorei(pname, param);
    }
  },

  // Filament binds textures only on units below its sampler limit, 16 vertex plus 16 fragment
  // samplers on WebGL2, but its state reset sweeps every unit that the context reports.
  glGetIntegerv__deps: ['$emscriptenWebGLGet'],
  glGetIntegerv: (name_, p) => {
    emscriptenWebGLGet(name_, p, {{{ cDefs.EM_FUNC_SIG_PARAM_I }}});
    if (name_ == 0x8B4D /* GL_MAX_COMBINED_TEXTURE_IMAGE_UNITS */ && HEAP32[p >> 2] > 32) HEAP32[p >> 2] = 32;
  },
});
