"""Readback: RenderTarget.read(), read(out=...), and pipelined reads with begin_read()."""

import gc
import threading
import time

import numpy as np
import pytest

import filly

pytestmark = pytest.mark.gpu

WIDTH, HEIGHT = 48, 32


@pytest.fixture
def target(renderer):
    return renderer.create_render_target(width=WIDTH, height=HEIGHT)


@pytest.fixture
def square(scene):
    """An unlit square that each frame moves and recolors, so that frames differ."""
    return scene.create_mesh(**filly.shapes.plane(0.5, 0.5), unlit=True, base_color=(1, 0, 0, 1))


def show(square, frame):
    square.position = (-0.6 + 0.1 * frame, 0.2, 0)
    square.material("mesh").base_color = ((frame * 37 % 256) / 255, (frame * 91 % 256) / 255, 0.5, 1)


def test_read_orientation_and_layout(renderer, scene, target, square):
    square.position = (0, 0.6, 0)  # upper part of the image
    renderer.render(scene, target)
    image = target.read()
    assert image.shape == (HEIGHT, WIDTH, 4) and image.dtype == np.uint8
    assert image.flags.c_contiguous and image.flags.writeable and image.flags.owndata is False
    # The first row is the top of the image.
    assert image[2, WIDTH // 2, 0] == 255
    assert image[HEIGHT - 3, WIDTH // 2, 0] == 0
    assert renderer.stats.readback_ms > 0


def test_read_into_out_matches_new_array(renderer, scene, target, square):
    show(square, 3)
    renderer.render(scene, target)
    expected = target.read()
    out = np.full((HEIGHT, WIDTH, 4), 7, np.uint8)
    assert target.read(out=out) is out
    np.testing.assert_array_equal(out, expected)
    # The same array again, after another frame, holds only the new frame.
    show(square, 9)
    renderer.render(scene, target)
    target.read(out=out)
    np.testing.assert_array_equal(out, target.read())
    assert not np.array_equal(out, expected)


def test_out_must_match_exactly(renderer, scene, target):
    renderer.render(scene, target)
    with pytest.raises(ValueError, match=r"shape \(32, 48, 4\)"):
        target.read(out=np.empty((WIDTH, HEIGHT, 4), np.uint8))
    with pytest.raises(TypeError, match="writable uint8"):
        target.read(out=np.empty((HEIGHT, WIDTH, 4), np.float32))
    readonly = np.empty((HEIGHT, WIDTH, 4), np.uint8)
    readonly.flags.writeable = False
    with pytest.raises(TypeError, match="writable uint8"):
        target.read(out=readonly)
    with pytest.raises(ValueError, match="C-contiguous"):
        target.read(out=np.empty((HEIGHT, WIDTH * 2, 4), np.uint8)[:, ::2])
    with pytest.raises(TypeError, match="writable uint8"):
        target.read(out=[[0] * 4] * (WIDTH * HEIGHT))
    with pytest.raises(TypeError):
        target.read(np.empty((HEIGHT, WIDTH, 4), np.uint8))  # keyword only


def test_begin_read_keeps_its_frame(renderer, scene, target, square):
    show(square, 1)
    renderer.render(scene, target)
    first_expected = target.read()
    first = target.begin_read()
    assert first.frame_number == renderer.stats.frames_rendered
    show(square, 5)
    renderer.render(scene, target)
    second = target.begin_read()
    assert second.frame_number == first.frame_number + 1
    # Later renders into the target do not change an earlier readback.
    np.testing.assert_array_equal(second.read(), target.read())
    np.testing.assert_array_equal(first.read(), first_expected)


def test_pipelined_reads_match_synchronous_reads(renderer, scene, target, square):
    frames = 12
    expected = []
    for frame in range(frames):
        show(square, frame)
        renderer.render(scene, target)
        expected.append(target.read())
    assert len({image.tobytes() for image in expected}) == frames
    for depth in (1, 2, 3):
        buffers = [np.empty((HEIGHT, WIDTH, 4), np.uint8) for _ in range(depth + 1)]
        pending, results = [], []
        for frame in range(frames):
            show(square, frame)
            renderer.render(scene, target)
            pending.append(target.begin_read())
            if len(pending) > depth:
                out = buffers[frame % len(buffers)]
                results.append(pending.pop(0).read(out=out).copy())
        results.extend(readback.read() for readback in pending)
        for got, want in zip(results, expected, strict=True):
            np.testing.assert_array_equal(got, want)


def test_msaa_and_direct_paths_read_the_same_way(renderer, scene, target, square):
    for options in ({"msaa": 4}, {"output_path": "direct"}):
        for name, value in options.items():
            setattr(scene, name, value)
        show(square, 4)
        renderer.render(scene, target)
        readback = target.begin_read()
        np.testing.assert_array_equal(readback.read(), target.read())
        scene.msaa = 1
        scene.output_path = "exact"


def test_ready_becomes_true_without_waiting(renderer, scene, target, square):
    renderer.render(scene, target)
    readback = target.begin_read()
    deadline = time.monotonic() + 5
    while not readback.ready():
        assert time.monotonic() < deadline
        time.sleep(0.001)
    assert readback.ready()
    np.testing.assert_array_equal(readback.read(), target.read())


def test_ready_does_not_report_an_earlier_copy(renderer, scene):
    # A readback that was polled until ready() leaves its buffer for the next one. A new copy
    # behind a frame cannot finish within the first ready() call: the driver thread has not run
    # the copy when the call returns. Before the fix, about half of these returned True.
    target = renderer.create_render_target(width=1920, height=1080)
    renderer.render(scene, target)
    early = 0
    for _ in range(20):
        renderer.render(scene, target)
        readback = target.begin_read()
        early += readback.ready()
        deadline = time.monotonic() + 5
        while not readback.ready():
            assert time.monotonic() < deadline
        readback.read()
    assert early == 0


def test_buffers_are_reused_safely(renderer, scene, target, square):
    images, readbacks = [], []
    for frame in range(4):
        show(square, frame)
        renderer.render(scene, target)
        images.append(target.read())
        readbacks.append(target.begin_read())
    # Out of order, and with readbacks closed or dropped unread, whose buffers later reads reuse.
    readbacks[1].close()
    readbacks[1].close()
    del readbacks[2]
    gc.collect()
    np.testing.assert_array_equal(readbacks[2].read(), images[3])
    np.testing.assert_array_equal(readbacks[0].read(), images[0])
    for frame in range(4, 10):
        show(square, frame)
        renderer.render(scene, target)
        expected = target.read()
        with target.begin_read() as readback:
            np.testing.assert_array_equal(readback.read(), expected)
        assert readback.closed


def test_readback_close_rules(renderer, scene, target):
    with pytest.raises(filly.FillyError, match="Render to the target"):
        target.begin_read()
    renderer.render(scene, target)
    readback = target.begin_read()
    assert not readback.closed
    readback.read()
    assert readback.closed
    for call in (readback.read, readback.ready):
        with pytest.raises(filly.FillyError, match="Readback is closed"):
            call()
    readback.close()
    assert readback.frame_number == renderer.stats.frames_rendered
    with pytest.raises(filly.FillyError, match="Readback is closed"):
        with readback:
            pass
    # Closing the target releases its buffers; its readbacks close with it.
    pending = target.begin_read()
    target.close()
    assert pending.closed
    with pytest.raises(filly.FillyError, match="Render target is closed"):
        pending.read()
    pending.close()


def test_readback_outlives_a_dropped_target(renderer, scene, square):
    def start():
        target = renderer.create_render_target(width=WIDTH, height=HEIGHT)
        renderer.render(scene, target)
        return target.read(), target.begin_read()

    expected, readback = start()
    gc.collect()
    np.testing.assert_array_equal(readback.read(), expected)


def test_renderer_close_closes_readbacks():
    renderer = filly.Renderer()
    scene = renderer.create_scene()
    camera = renderer.create_camera()
    camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
    camera.position = (0, 0, 3)
    scene.camera = camera
    target = renderer.create_render_target(width=WIDTH, height=HEIGHT)
    renderer.render(scene, target)
    readback = target.begin_read()
    renderer.close()
    assert readback.closed
    with pytest.raises(filly.FillyError, match="Renderer is closed"):
        readback.read()
    readback.close()


def test_readback_is_bound_to_the_creating_thread(renderer, scene, target):
    renderer.render(scene, target)
    readback = target.begin_read()
    errors = []

    def other():
        for call in (readback.read, readback.ready, target.begin_read):
            try:
                call()
            except filly.FillyError as error:
                errors.append(str(error))

    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    assert len(errors) == 3 and all("creating thread" in error for error in errors)
    readback.read()


def test_many_targets_and_sizes(renderer, scene, square):
    show(square, 2)
    for width, height in ((1, 1), (3, 5), (17, 9), (640, 480)):
        target = renderer.create_render_target(width=width, height=height)
        renderer.render(scene, target)
        image = target.read()
        assert image.shape == (height, width, 4)
        np.testing.assert_array_equal(target.begin_read().read(), image)
        target.close()


def test_new_arrays_never_share_storage_with_live_arrays(renderer, scene, target, square):
    # read() reuses the storage of an array that Python has freed; arrays and views that are
    # alive keep their pixels.
    show(square, 1)
    renderer.render(scene, target)
    first = target.read()
    view = target.read()[..., :3]
    kept = first.copy(), view.copy()
    for frame in range(2, 8):
        show(square, frame)
        renderer.render(scene, target)
        image = target.read()
        expected = image.copy()
        del image
        reused = target.read()
        np.testing.assert_array_equal(reused, expected)
        np.testing.assert_array_equal(first, kept[0])
        np.testing.assert_array_equal(view, kept[1])
        assert not np.shares_memory(reused, first) and not np.shares_memory(reused, view)
