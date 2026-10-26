% Batch calls: setTransforms and setMorphWeights on Node arrays, setParameters on Material arrays.
r = filly.Renderer();
scene = r.createScene();
cam = r.createCamera();
scene.camera = cam;
cam.position = [0 0 8];
cam.lookAt([0 0 0]);
scene.background = [0 0 0 1];
target = r.createRenderTarget(64, 64);
scene.addDirectionalLight([0.3 -0.5 -1]);

% Object arrays grow by indexed assignment: Octave cannot concatenate classdef objects ([a b]).
shape = filly.shapes.box('Width', 0.5, 'Height', 0.5, 'Depth', 0.5);
count = 5;
models = scene.createMesh(shape.positions, shape.indices, 'Normals', shape.normals);
nodes = models(1).root;
for i = 2:count
    models(i) = scene.createMesh(shape.positions, shape.indices, 'Normals', shape.normals);
    nodes(i) = models(i).root;
end
assert(isempty(filly.Object.wrap(@filly.Node, r, zeros(1, 0, 'uint64'))));

% Transforms: 4 x 4 x N, the same matrices as one property set per node.
T = repmat(eye(4), 1, 1, count);
for i = 1:count
    T(1:3, 4, i) = [i - 3, 0.5 * (i - 3), 0];
end
setTransforms(nodes, T);
for i = 1:count
    assert(max(max(abs(models(i).transform - T(:, :, i)))) < 1e-6);
end
r.render(scene, target);
batched = target.read();
for i = 1:count
    models(i).transform = eye(4);
end
for i = 1:count
    models(i).transform = T(:, :, i);
end
r.render(scene, target);
assert(isequal(target.read(), batched), 'batch and single transforms must render the same image');
% single input works too. (Octave cannot call a method of an object array with dot syntax.)
setTransforms(nodes, single(T));

% An invalid matrix anywhere in the batch changes nothing.
U = T;
U(1:3, 4, :) = 0;
U(1, 1, count) = NaN;
expect_error(@() setTransforms(nodes, U), 'filly:invalidArgument');
for i = 1:count
    assert(max(max(abs(models(i).transform - T(:, :, i)))) < 1e-6);
end
% The count must match. Nodes of other scenes are fine; nodes of other renderers are not.
expect_error(@() setTransforms(nodes, T(:, :, 1:2)), 'filly:invalidArgument');
other = r.createScene();
neighbor = other.createMesh([0 0 0; 1 0 0; 0 1 0], [1 2 3]);
pair = nodes(1);
pair(2) = neighbor.root;
setTransforms(pair, T(:, :, 1:2));
assert(max(max(abs(neighbor.transform - T(:, :, 2)))) < 1e-6);
r2 = filly.Renderer();
scene2 = r2.createScene();
stranger = scene2.createMesh([0 0 0; 1 0 0; 0 1 0], [1 2 3]);
pair(2) = stranger.root;
expect_error(@() setTransforms(pair, T(:, :, 1:2)), 'filly:invalidArgument');
close(r2);
setTransforms(nodes([]), zeros(4, 4, 0));

% Material parameters: one row per material.
for i = 1:count
    meshNodes = models(i).nodes;
    material = meshNodes(1).material();
    if i == 1, materials = material; else, materials(i) = material; end
end
colors = [linspace(0, 1, count)', zeros(count, 1), ones(count, 1), ones(count, 1)];
setParameters(materials, 'baseColor', colors);
for i = 1:count
    assert(max(abs(materials(i).baseColor - colors(i, :))) < 1e-6);
end
setParameters(materials, 'roughness', linspace(0, 1, count));
assert(abs(materials(count).roughness - 1) < 1e-6);
setParameters(materials, 'metallic', linspace(0, 1, count)');
assert(abs(materials(1).metallic) < 1e-6);
setParameters(materials, 'emissive', repmat([0.5 0.25 0], count, 1));
assert(max(abs(materials(2).emissive - [0.5 0.25 0])) < 1e-6);
% Out-of-range values change nothing.
bad = colors;
bad(count, 1) = 2;
expect_error(@() setParameters(materials, 'baseColor', bad), 'filly:invalidArgument');
assert(max(abs(materials(1).baseColor - colors(1, :))) < 1e-6);
expect_error(@() setParameters(materials, 'baseColor', colors(:, 1:3)), 'filly:invalidArgument');
expect_error(@() setParameters(materials, 'shininess', colors), 'filly:invalidArgument');

% Morph weights: one row per node, one column per target.
morphScene = r.createScene();
morphScene.camera = cam;
morphScene.background = [0 0 0 1];
first = morphScene.load(morph_glb());
second = morphScene.load(morph_glb());
first.position = [-1.5 0 0];
second.position = [0.5 0 0];
morphNodes = first.node('morph');
morphNodes(2) = second.node('morph');
assert(morphNodes(1).morphTargetCount == 1);
morphScene.addDirectionalLight([0 0 -1]);
r.render(morphScene, target);
rest = target.read();
setMorphWeights(morphNodes, [1; 0.5]);
r.render(morphScene, target);
batched = target.read();
assert(~isequal(batched, rest), 'morph weights must move the triangles');
morphNodes(1).setMorphWeights(0);
morphNodes(2).setMorphWeights(0);
morphNodes(1).setMorphWeights(1);
morphNodes(2).setMorphWeights(0.5);
r.render(morphScene, target);
assert(isequal(target.read(), batched), 'batch and single morph weights must render the same image');
expect_error(@() setMorphWeights(morphNodes, [1 1; 1 1]), 'filly:invalidArgument');
expect_error(@() setMorphWeights(morphNodes, [1; NaN]), 'filly:invalidArgument');
mixed = morphNodes(1);
mixed(2) = first.root;
expect_error(@() setMorphWeights(mixed, [1; 1]), 'filly:invalidArgument');
