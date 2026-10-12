"""Shared-texture integration for PsychoPy on Windows/WGL and Linux/GLX."""

import ctypes
import sys
import weakref

import numpy
from psychopy import visual
from psychopy.visual import shaders
from pyglet import gl

from .. import InteropError, current_gl_context
from .._native import _create_host_texture, _delete_host_texture
from ._host import HostTargetBase, shared_renderer

__all__ = ["HostTarget", "create_renderer"]

# One ImageStim program for premultiplied color per window, which lives as long as the window's
# context. Compiling and linking it took most of the time of each create_image_stim().
_programs = weakref.WeakKeyDictionary()


def _shared_program(win):
    program = _programs.get(win)
    if program is None or not gl.glIsProgram(program):
        # Filament stores premultiplied RGB. ImageStim expects straight RGB before
        # applying its color, opacity, mask, and the window's blend function.
        fragment = shaders.fragImageStim.replace(
            "vec4 maskFrag =", "textureFrag.rgb = textureFrag.a > 0.0 ? textureFrag.rgb / textureFrag.a : vec3(0.0);\nvec4 maskFrag =")
        program = shaders.compileProgram(shaders.vertSimple, fragment)
        _programs[win] = program
    return program


def _is_open(win):
    # PsychoPy keeps winHandle after close(); pyglet clears the handle's context.
    handle = getattr(win, "winHandle", None)
    return (not getattr(win, "_closed", False) and handle is not None
            and getattr(handle, "context", None) is not None)


def _current(win):
    if sys.platform not in ("win32", "linux") or win.winType != "pyglet":
        raise InteropError("The adapter requires pyglet with Windows/WGL or Linux/GLX")
    if not _is_open(win):
        raise InteropError("The PsychoPy window is closed")
    win._setCurrent()
    # PsychoPy skips the switch when it believes the window is already current, which is wrong
    # after other code switched contexts through pyglet.
    if gl.current_context is not win.winHandle.context:
        win.winHandle.switch_to()


def create_renderer(win):
    """Create a Filament engine sharing the window's native OpenGL context."""
    _current(win)
    return shared_renderer("PsychoPy")


class HostTarget(HostTargetBase):
    """A PsychoPy-window RGBA8 texture that Filament renders into.

    ``texture`` is the GL texture name (int), 0 after close. The size defaults to the
    window's framebuffer size. ``create_image_stim()`` returns an ImageStim that samples
    the texture. ``draw()`` draws the texture with such a stimulus in pixel units.
    """

    texture = 0
    _internal = None

    def __init__(self, renderer, win, *, width=None, height=None):
        self._win = win
        self._stimuli = weakref.WeakSet()
        super().__init__(renderer, width, height)

    def _host_open(self):
        return _is_open(self._win)

    def _make_host_current(self):
        _current(self._win)

    def _default_size(self):
        return tuple(self._win.frameBufferSize)

    def _create_host_texture(self, width, height):
        self.texture = _create_host_texture(width, height)
        return self.texture

    def _draw(self, x, y, width, height, blend):
        # One internal stimulus for each draw mode: the legacy GL path records the program in
        # a display list.
        if self._internal is None:
            self._internal = {}
        stimulus = self._internal.get(blend)
        if stimulus is None:
            stimulus = self._internal[blend] = self.create_image_stim()
            stimulus._copy = not blend
        columns, rows = self._win.size
        placement = (width, height), (x + (width - columns) / 2, y + (height - rows) / 2)
        # Assignment makes ImageStim rebuild its vertices, so skip it when nothing moved.
        if placement != stimulus._placement:
            stimulus._placement = placement
            stimulus.size, stimulus.pos = placement
        stimulus.draw()

    def _release_host_resources(self, host_open):
        for stimulus in list(self._stimuli):
            stimulus._detach(host_open)
        if host_open:
            _delete_host_texture(self.texture)
        # The internal stimuli refer back to this target; drop the cycle.
        self.texture, self._internal = 0, None

    def create_image_stim(self, *, size=None, pos=(0, 0), units="pix"):
        """Return a new ImageStim, in the target's window, that samples this texture directly.

        Inside ``acquire()``, several stimuli can draw the same frame.
        """
        if self._closed:
            raise InteropError("Host target is closed")
        if size is None:
            if units != "pix":
                raise ValueError("Specify size when units are not 'pix'")
            size = (self.width, self.height)
        stimulus = _SharedImageStim(self, size=size, pos=pos, units=units)
        self._stimuli.add(stimulus)
        return stimulus


class _SharedImageStim(visual.ImageStim):
    # HostTarget.draw(blend=False) copies: the window's own program, without blending.
    _copy = False
    _placement = None

    @property
    def image(self):
        if getattr(self, "_external_texture", False):
            return self._shared_target
        return self.__dict__.get("image")

    @image.setter
    def image(self, value):
        if not getattr(self, "_external_texture", False):
            visual.ImageStim.image.__set__(self, value)
        # ImageStim re-sets its own image (_imName) after some changes. An upload would overwrite
        # the target's texture.
        elif value is self._shared_target or value is self._imName:
            self._needTextureUpdate = False
        else:
            raise InteropError("A shared stimulus cannot replace its target image")

    def __init__(self, target, **kwargs):
        self._shared_target = target
        self._external_texture = False
        # An RGB placeholder, not the target: ImageStim in PsychoPy 2024.2 does not take texture
        # providers (colorTexture). RGB selects the window's ImageStim program, not the luminance
        # one.
        super().__init__(target._win, image=numpy.zeros((1, 1, 3), numpy.float32), autoLog=False, **kwargs)
        gl.glDeleteTextures(1, ctypes.byref(self._texID))
        self._texID = gl.GLuint(target.texture)
        self._external_texture = True
        self.flipVert = False
        self._shared_program = _shared_program(target._win)
        # The legacy GL path records the program and texture in a display list. Rebuild it in
        # draw(), where the premultiplied program is in place.
        self._needUpdate = True

    def draw(self, win=None):
        target = self._shared_target
        if win is not None and win is not target._win:
            raise InteropError("Draw the texture in its original PsychoPy window")
        if self._copy:
            blending = gl.glIsEnabled(gl.GL_BLEND)
            gl.glDisable(gl.GL_BLEND)
            try:
                with target.acquire():
                    super().draw(target._win)
            finally:
                if blending:
                    gl.glEnable(gl.GL_BLEND)
            return
        if target._win.blendMode != "avg":
            raise InteropError("Shared stimuli require PsychoPy blendMode='avg'")
        with target.acquire():
            previous = target._win._progImageStim
            try:
                target._win._progImageStim = self._shared_program
                super().draw(target._win)
            finally:
                target._win._progImageStim = previous

    def _detach(self, host_open):
        if host_open:
            self.clearTextures()
            return
        # Without the host context, a GL delete would fail or reach another
        # context. These objects go away with the context's share group.
        self._maskID = gl.GLuint()
        self._shared_program = None
        self._texID = gl.GLuint()

    def clearTextures(self):
        if not getattr(self, "_external_texture", False):
            return super().clearTextures()
        target = self._shared_target
        # ImageStim.__del__ calls this during garbage collection, when another window's context
        # can be current. Names deleted there could belong to that window's objects.
        if not (_is_open(target._win) and current_gl_context() == target._context):
            return self._detach(False)
        # The target owns the color texture and the window owns the program; ImageStim owns
        # only its mask.
        if getattr(self, "_maskID", None) and self._maskID.value:
            gl.glDeleteTextures(1, ctypes.byref(self._maskID))
            self._maskID = gl.GLuint()
        self._shared_program = None
        self._texID = gl.GLuint()
