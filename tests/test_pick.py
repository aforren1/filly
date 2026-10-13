"""Picking: which model surface the scene shows at a target pixel, and where it is."""

import ctypes
import math
import time

import numpy as np
import pytest

import filly
from test_animation_pointer import accessor
from test_features import pack, unpack

pytestmark = pytest.mark.gpu

SIZE = 64


def answer(renderer, scene, target, *points, **options):
    queries = [renderer.pick(scene, x, y) for x, y in points]
    renderer.render(scene, target, **options)
    renderer.finish()
    assert all(query.ready() for query in queries)
    return queries if len(queries) > 1 else queries[0]


def pixel(world_x, world_y, size=SIZE):
    """The target pixel that shows (world_x, world_y) through the conftest camera ([-1, 1] square)."""
    return (world_x + 1) / 2 * size, (world_y + 1) / 2 * size


@pytest.fixture
def target(renderer):
    return renderer.create_render_target(width=SIZE, height=SIZE)


@pytest.fixture
def layers(scene):
    """A red square in front of a larger green one."""
    back = scene.create_mesh(**filly.shapes.plane(1.6, 1.6), unlit=True, base_color=(0, 1, 0, 1))
    back.position = (0, 0, -0.5)
    front = scene.create_mesh(**filly.shapes.plane(0.8, 0.8), unlit=True, base_color=(1, 0, 0, 1))
    return front, back


def test_front_surface_wins(renderer, scene, target, layers):
    front, back = layers
    center, edge, outside = answer(renderer, scene, target, pixel(0, 0), pixel(0.6, 0), pixel(0.9, 0.9))
    hit = center.result
    assert hit.model == front and hit.node == front.node(0)
    assert hit.model != back and hash(hit.model) == hash(front)
    assert repr(hit).startswith("<PickResult node=<Node 0")
    # The conftest camera is at z = 3, looking down -z.
    np.testing.assert_allclose(hit.position, (1 / 64, 1 / 64, 0), atol=1e-5)
    assert hit.depth == pytest.approx(3, abs=1e-5)
    assert edge.result.model == back
    assert edge.result.position[2] == pytest.approx(-0.5, abs=1e-5)
    assert edge.result.depth == pytest.approx(3.5, abs=1e-5)
    assert outside.result is None
    frame = renderer.stats.frames_rendered
    assert center.frame_number == edge.frame_number == outside.frame_number == frame


def test_query_belongs_to_the_next_render(renderer, scene, target, layers):
    query = renderer.pick(scene, *pixel(0, 0))
    assert not query.ready() and query.frame_number is None
    with pytest.raises(filly.FillyError, match="no answer yet"):
        query.result
    renderer.render(scene, target)
    taken = renderer.stats.frames_rendered
    renderer.render(scene, target)
    renderer.finish()
    assert query.ready() and query.frame_number == taken
    assert query.result.model == layers[0]


def test_answer_arrives_without_finish(renderer, scene, target, layers):
    # After the first pick of the renderer: without prepare(), it has a one-time cost (45 to 60 ms
    # on Intel Iris Xe).
    answer(renderer, scene, target, pixel(0, 0))
    answer_frames = None
    query = renderer.pick(scene, *pixel(0, 0))
    renderer.render(scene, target)
    for answer_frames in range(1, 11):
        time.sleep(1 / 60)
        if query.ready():
            break
        renderer.render(scene, target)
    assert query.ready() and answer_frames <= 4
    assert query.result.model == layers[0]


def test_answer_arrives_without_more_renders(renderer, scene, target, layers):
    # Hosts that render only after changes poll ready() without rendering.
    answer(renderer, scene, target, pixel(0, 0))
    query = renderer.pick(scene, *pixel(0, 0))
    renderer.render(scene, target)
    frames = renderer.stats.frames_rendered
    for polls in range(1, 31):
        time.sleep(1 / 60)
        if query.ready():
            break
    assert query.ready() and polls <= 4, polls
    assert renderer.stats.frames_rendered == frames
    assert query.result.model == layers[0]


def test_prepare_picks_once_out_of_sight(renderer, scene, target, layers):
    # prepare() pays the first pick's driver cost in a hidden frame of its own: the caller's
    # queries, frame count, and camera stay as they were.
    camera = scene.camera
    queued = renderer.pick(scene, *pixel(0, 0))
    for _ in range(2):
        preparation = renderer.prepare(scene)
        deadline = time.monotonic() + 30
        while not preparation.ready():
            assert time.monotonic() < deadline, f"{preparation.pending} still pending"
            time.sleep(0.005)
        assert preparation.pending == 0
    assert renderer.stats.frames_rendered == 0 and renderer.stats.gpu_frame_number is None
    assert not queued.ready() and queued.frame_number is None
    assert scene.camera == camera
    renderer.render(scene, target)
    renderer.finish()
    assert queued.ready() and queued.frame_number == 1 and queued.result.model == layers[0]


def test_answer_describes_the_rendered_frame(renderer, scene, target, layers):
    front, _ = layers
    query = renderer.pick(scene, *pixel(0, 0))
    renderer.render(scene, target)
    # Edits after the render do not change its answer.
    front.visible = False
    renderer.render(scene, target)
    renderer.finish()
    assert query.result.model == front
    assert answer(renderer, scene, target, pixel(0, 0)).result.model == layers[1]


def test_viewport_and_target_pixels(renderer, scene, layers):
    front, back = layers
    target = renderer.create_render_target(width=2 * SIZE, height=SIZE)
    # The right half shows the conftest camera's square; pixels are counted in the whole target.
    x, y = pixel(0, 0)
    inside, left = answer(renderer, scene, target, (SIZE + x, y), (x, y), viewport=(SIZE, 0, SIZE, SIZE))
    assert inside.result.model == front
    np.testing.assert_allclose(inside.result.position, (1 / 64, 1 / 64, 0), atol=1e-5)
    assert left.result is None and left.frame_number == inside.frame_number
    # Fractional coordinates name the pixel that contains them.
    a, b = answer(renderer, scene, target, (SIZE + 32.0, 32.0), (SIZE + 32.99, 32.99), viewport=(SIZE, 0, SIZE, SIZE))
    assert a.result.position == b.result.position


@pytest.mark.parametrize("ortho", [False, True])
def test_position_on_a_tilted_plane(renderer, ortho):
    scene = renderer.create_scene()
    plane = scene.create_mesh(**filly.shapes.plane(6, 6), unlit=True)
    plane.position = (0.2, -0.1, -0.3)
    plane.node(0).rotation_euler_deg = (-55, 20, 0)
    camera = renderer.create_camera()
    if ortho:
        camera.set_orthographic(height=3, near=0.5, far=50)
    else:
        camera.set_perspective(fov_y_deg=40, near=0.1, far=50)
    eye = np.array([0.5, 1.0, 4.0])
    camera.position = tuple(eye)
    camera.look_at((0, 0, 0))
    scene.camera = camera
    width, height = 160, 120
    target = renderer.create_render_target(width=width, height=height)
    points = [(80, 60), (10.5, 7), (140, 20), (33, 90)]
    queries = answer(renderer, scene, target, *points)
    world = np.array(camera.transform)
    right, up, back = world[:3, 0], world[:3, 1], world[:3, 2]
    model = np.array(plane.transform) @ np.array(plane.node(0).transform)
    normal, origin = model[:3, 2], model[:3, 3]
    for (x, y), query in zip(points, queries):
        hit = query.result
        u = 2 * (math.floor(x) + 0.5) / width - 1
        v = 2 * (math.floor(y) + 0.5) / height - 1
        if ortho:
            start = eye + u * 1.5 * width / height * right + v * 1.5 * up
            direction = -back
        else:
            half = math.tan(math.radians(20))
            start = eye
            direction = u * half * width / height * right + v * half * up - back
        t = np.dot(origin - start, normal) / np.dot(direction, normal)
        expected = start + t * direction
        # The picking pass stores float clip depth. Errors were up to 9e-5 units on both test
        # GPUs at depths of 3 to 7 units, about 1/450 of a pixel; the tolerance is 1/80.
        np.testing.assert_allclose(hit.position, expected, atol=5e-4)
        assert hit.depth == pytest.approx(np.dot(expected - eye, -back), abs=5e-4)


def test_box_faces(renderer, scene, target):
    box = scene.create_mesh(**filly.shapes.box(0.8, 0.8, 0.8), unlit=True)
    box.position = (0.1, 0, 0)
    hit = answer(renderer, scene, target, pixel(0.1, 0.2)).result
    assert hit.model == box
    assert hit.position[2] == pytest.approx(0.4, abs=1e-5)


def test_mask_cutout_passes_through(renderer, scene, target):
    back = scene.create_mesh(**filly.shapes.plane(1.8, 1.8), unlit=True, base_color=(0, 1, 0, 1))
    back.position = (0, 0, -0.5)
    shape = filly.shapes.plane(1.6, 1.6)
    # Alpha rises from 0 at the left edge to 1 at the right: the left half is cut out.
    colors = np.ones((len(shape["positions"]), 4), dtype=np.float32)
    colors[:, 3] = (shape["positions"][:, 0] + 0.8) / 1.6
    front = scene.create_mesh(**shape, colors=colors, unlit=True, alpha_mode="mask")
    left, right = answer(renderer, scene, target, pixel(-0.5, 0), pixel(0.5, 0))
    assert left.result.model == back
    assert right.result.model == front


@pytest.mark.parametrize("blended", [False, True])
def test_blended_surfaces(renderer, scene, target, blended):
    back = scene.create_mesh(**filly.shapes.plane(1.6, 1.6), unlit=True, base_color=(0, 1, 0, 1))
    back.position = (0, 0, -0.5)
    glass = scene.create_mesh(**filly.shapes.plane(0.8, 0.8), unlit=True, base_color=(1, 0, 0, 0.3), alpha_mode="blend")
    assert scene.pick_blended is False
    scene.pick_blended = blended
    hit = answer(renderer, scene, target, pixel(0, 0)).result
    assert hit.model == (glass if blended else back)


@pytest.mark.parametrize("msaa", [1, 4])
@pytest.mark.parametrize("ssao", [False, True])
def test_msaa_and_ssao(renderer, scene, layers, msaa, ssao):
    scene.msaa = msaa
    scene.ssao = ssao
    target = renderer.create_render_target(width=128, height=128)
    front, back = layers
    center, edge = answer(renderer, scene, target, pixel(0, 0, 128), pixel(0.6, 0, 128))
    assert center.result.model == front and edge.result.model == back
    # With SSAO, picks read the half-resolution structure buffer: the hit is the surface at the
    # center of the 2 x 2 pixel block.
    offset = 2 / 128 if ssao else 1 / 128
    np.testing.assert_allclose(center.result.position, (offset, offset, 0), atol=1e-5)


def test_small_viewports_are_rejected(renderer, scene, layers):
    target = renderer.create_render_target(width=128, height=128)
    renderer.pick(scene, 10, 10)
    with pytest.raises(ValueError, match="at least 32 x 32"):
        renderer.render(scene, target, viewport=(0, 0, 31, 64))
    scene.ssao = True
    with pytest.raises(ValueError, match="at least 64 x 64 pixels with ssao"):
        renderer.render(scene, target, viewport=(0, 0, 64, 63))
    # The queued pick waits for a render that can take it.
    renderer.render(scene, target, viewport=(0, 0, 64, 64))
    renderer.render(scene, target, viewport=(0, 0, 16, 16))


def test_instanced_children_are_nodes(renderer, scene, target, triangle_glb):
    doc, binary = unpack(triangle_glb)
    doc["nodes"][0].update(scale=[0.4] * 3, translation=[0.1, 0.1, 0])
    translations = accessor(doc, binary, [-1, 0, 0, 1, 0, 0], "VEC3")
    scales = accessor(doc, binary, [0.5, 1, 1, 1, 0.5, 1], "VEC3")
    doc["nodes"][0]["extensions"] = {"EXT_mesh_gpu_instancing": {"attributes": {"TRANSLATION": translations, "SCALE": scales}}}
    doc["extensionsUsed"].append("EXT_mesh_gpu_instancing")
    doc["extensionsRequired"].append("EXT_mesh_gpu_instancing")
    model = scene.load(pack(doc, binary), strict=True)
    # Instance 1 spans x -0.42..-0.18 around -0.3; instance 2 spans x 0.26..0.74 around 0.5.
    left, right = answer(renderer, scene, target, pixel(-0.3, 0.15), pixel(0.5, 0.1))
    assert left.result.model == model == right.result.model
    assert (left.result.node.index, right.result.node.index) == (1, 2)
    assert left.result.node.parent == model.node(0)


def skinned(triangle_glb):
    doc, binary = unpack(triangle_glb)
    joints = accessor(doc, binary, [0] * 12, "VEC4", component=5121)
    weights = accessor(doc, binary, [1, 0, 0, 0] * 3, "VEC4")
    doc["meshes"][0]["primitives"][0]["attributes"].update(JOINTS_0=joints, WEIGHTS_0=weights)
    doc["nodes"][0]["skin"] = 0
    doc["nodes"].append({"name": "joint"})
    doc["scenes"][0]["nodes"].append(1)
    doc["skins"] = [{"joints": [1]}]
    times = accessor(doc, binary, [0, 1])
    shift = accessor(doc, binary, [0, 0, 0, 0.7, 0, 0], "VEC3")
    doc["animations"] = [{"name": "slide", "samplers": [{"input": times, "output": shift}],
                          "channels": [{"sampler": 0, "target": {"node": 1, "path": "translation"}}]}]
    return pack(doc, binary)


def test_skinned_pose(renderer, scene, target, triangle_glb):
    model = scene.load(skinned(triangle_glb), strict=True)
    rest, moved = pixel(-0.45, -0.35), pixel(0.8, -0.35)
    before = answer(renderer, scene, target, rest, moved)
    assert before[0].result.node == model.node(0) and before[1].result is None
    # The skin follows its joint; the node that carries the mesh is the hit.
    model.apply_animation("slide", 1, loop=False)
    after = answer(renderer, scene, target, rest, moved)
    assert after[0].result is None
    assert after[1].result.node == model.node(0)
    model.node("joint").position = (0, 0, 0)
    assert answer(renderer, scene, target, rest).result.node == model.node(0)


def test_morph_targets(renderer, scene, target, triangle_glb):
    doc, binary = unpack(triangle_glb)
    offsets = accessor(doc, binary, [0.6, 0, 0] * 3, "VEC3")
    doc["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": offsets}]
    model = scene.load(pack(doc, binary), strict=True)
    left = pixel(-0.45, -0.3)
    assert answer(renderer, scene, target, left).result.model == model
    model.node(0).set_morph_weights([1])
    assert answer(renderer, scene, target, left).result is None
    assert answer(renderer, scene, target, pixel(0.85, -0.3)).result.model == model


def test_skybox_and_lights_are_not_hits(renderer, scene, target):
    scene.environment = renderer.create_environment(np.full((8, 16, 3), 0.5, dtype="f"))
    scene.environment_visible = True
    scene.add_point_light(position=(0, 0, 1))
    assert answer(renderer, scene, target, pixel(0, 0)).result is None


def test_closed_models_and_scenes(renderer, scene, target, layers):
    front, back = layers
    query = renderer.pick(scene, *pixel(0, 0))
    renderer.render(scene, target)
    renderer.finish()
    front.close()
    # The node lookup happens at the first ready(); a model closed before then is no hit.
    assert query.ready() and query.result is None
    queued = renderer.pick(scene, *pixel(0, 0))
    scene.close()
    assert queued.ready()
    with pytest.raises(filly.FillyError, match="scene closed"):
        queued.result


def test_arguments(renderer, scene):
    other = filly.Renderer()
    try:
        with pytest.raises(ValueError, match="same renderer|belong"):
            other.pick(scene, 1, 1)
    finally:
        other.close()
    with pytest.raises(ValueError, match="finite"):
        renderer.pick(scene, math.nan, 1)
    scene.close()
    with pytest.raises(filly.FillyError, match="Scene is closed"):
        renderer.pick(scene, 1, 1)


@pytest.mark.interop
def test_imported_target(triangle_glb):
    pyglet = pytest.importorskip("pyglet")
    from pyglet import gl
    window = pyglet.window.Window(width=64, height=64, visible=False)
    window.switch_to()
    texture = gl.GLuint()
    gl.glGenTextures(1, ctypes.byref(texture))
    gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
    gl.glTexStorage2D(gl.GL_TEXTURE_2D, 1, gl.GL_RGBA8, 64, 64)
    try:
        with filly.Renderer(shared_context=filly.current_gl_context()) as renderer:
            scene = renderer.create_scene()
            camera = renderer.create_camera()
            camera.set_orthographic(left=-1, right=1, bottom=-1, top=1, near=0.1, far=10)
            camera.position = (0, 0, 3)
            camera.look_at((0, 0, 0))
            scene.camera = camera
            model = scene.load(triangle_glb)
            target = renderer.import_gl_target(texture.value, width=64, height=64)
            hit = answer(renderer, scene, target, pixel(0, 0)).result
            assert hit.model == model and hit.node.name == "triangle"
            target.close()
    finally:
        window.switch_to()
        gl.glDeleteTextures(1, ctypes.byref(texture))
        window.close()
