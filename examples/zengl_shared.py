"""Spin Suzanne in a pyglet window through a shared image drawn by zengl."""

import zengl

import filly
from filly.integrations.pyglet import create_renderer
from filly.integrations.zengl import SharedTarget
from pyglet_shared import open_window, parse_args, run


def main():
    args = parse_args(__doc__)
    filly.set_log_level("warning")
    window = open_window(args, "filly + zengl")
    renderer = create_renderer(window)
    # zengl loads GL functions from the current WGL/GLX context and keeps one context per process.
    ctx = zengl.context()
    width, height = window.get_framebuffer_size()
    target = SharedTarget(renderer, ctx, width, height)

    def draw():
        # new_frame() drops zengl's cached bindings, which pyglet may have changed.
        ctx.new_frame()
        target.draw()
        ctx.end_frame()

    run(window, renderer, target, draw, args)
    zengl.cleanup()
    window.close()


if __name__ == "__main__":
    main()
