"""Spin and animate the wooden horse in a pyglet window through a shared texture drawn by moderngl."""

import moderngl

import filly
from filly.integrations.moderngl import HostTarget, create_renderer
from pyglet_shared import open_window, parse_args, run


def main():
    args = parse_args(__doc__)
    filly.set_log_level("warning")
    window = open_window(args, "filly + moderngl")
    # moderngl attaches to the current context, which open_window() made current; it does not
    # create one.
    ctx = moderngl.create_context()
    renderer = create_renderer()
    target = HostTarget(renderer, ctx)

    def draw():
        ctx.clear(0.02, 0.03, 0.05)
        target.draw()

    run(window, renderer, target, draw, args)
    ctx.release()
    window.close()


if __name__ == "__main__":
    main()
