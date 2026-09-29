"""Spin Suzanne in a pyglet window through a shared texture drawn by moderngl."""

import moderngl

import filly
from filly.integrations.moderngl import SharedTarget
from filly.integrations.pyglet import create_renderer
from pyglet_shared import open_window, parse_args, run


def main():
    args = parse_args(__doc__)
    filly.set_log_level("warning")
    window = open_window(args, "filly + moderngl")
    renderer = create_renderer(window)
    # moderngl attaches to the current context; it does not create one.
    ctx = moderngl.create_context()
    width, height = window.get_framebuffer_size()
    target = SharedTarget(renderer, ctx, width, height)

    def draw():
        ctx.clear(0.02, 0.03, 0.05)
        target.draw()

    run(window, renderer, target, draw, args)
    ctx.release()
    window.close()


if __name__ == "__main__":
    main()
