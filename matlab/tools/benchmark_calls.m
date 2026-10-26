function results = benchmark_calls(count)
% BENCHMARK_CALLS  Measure the cost of filly calls from MATLAB or Octave.
%   results = benchmark_calls() reports medians of 7 runs, in microseconds per call unless the
%   field name ends in Ms (milliseconds):
%   - a bare gateway call, property reads and writes through filly_mex and through the +filly
%     classes, and a method call (camera.lookAt);
%   - one frame of transform and material updates of `count` nodes (default 200), one call
%     per node and with the batch calls;
%   - render() plus read() of a 320 x 240 target, and read() of a 1920 x 1080 target.
%   The loops are written out, not passed as function handles, because MATLAB adds the cost of
%   a handle call to each iteration.
if nargin < 1, count = 200; end
filly.setLogLevel('error');
r = filly.Renderer();
scene = r.createScene();
cam = r.createCamera();
scene.camera = cam;
cam.position = [0 0 20];
cam.lookAt([0 0 0]);
scene.addDirectionalLight([0 -1 -1]);
shape = filly.shapes.box('Width', 0.2, 'Height', 0.2, 'Depth', 0.2);
% Indexed assignment, not [a b]: Octave cannot concatenate classdef objects.
for i = 1:count
    model = scene.createMesh(shape.positions, shape.indices, 'Normals', shape.normals);
    meshNodes = model.nodes;
    if i == 1
        models = model;
        materials = meshNodes(1).material();
        nodes = model.root;
    else
        models(i) = model;
        materials(i) = meshNodes(1).material();
        nodes(i) = model.root;
    end
end
model = models(1);
id = model.Id;
T = repmat(eye(4), 1, 1, count);
T(1, 4, :) = reshape(linspace(-5, 5, count), 1, 1, count);
colors = rand(count, 4);
colors(:, 4) = 1;
position = [1 2 3];
n = 2000;
runs = 7;
samples = zeros(runs, 12);
small = r.createRenderTarget(320, 240);
large = r.createRenderTarget(1920, 1080);
r.render(scene, large);
for k = 1:runs
    t = tic;
    for i = 1:n, filly_mex('handleCount'); end
    samples(k, 1) = toc(t) / n * 1e6;
    t = tic;
    for i = 1:n, v = filly_mex('get', id, 'visible'); end %#ok<NASGU>
    samples(k, 2) = toc(t) / n * 1e6;
    t = tic;
    for i = 1:n, filly_mex('set', id, 'position', position); end
    samples(k, 3) = toc(t) / n * 1e6;
    t = tic;
    for i = 1:n, v = model.visible; end %#ok<NASGU>
    samples(k, 4) = toc(t) / n * 1e6;
    t = tic;
    for i = 1:n, model.position = position; end
    samples(k, 5) = toc(t) / n * 1e6;
    t = tic;
    for i = 1:n, cam.lookAt(position); end
    samples(k, 6) = toc(t) / n * 1e6;
    t = tic;
    for frame = 1:10
        for i = 1:count, models(i).transform = T(:, :, i); end
    end
    samples(k, 7) = toc(t) / 10 * 1e3;
    t = tic;
    for frame = 1:100, setTransforms(nodes, T); end
    samples(k, 8) = toc(t) / 100 * 1e3;
    t = tic;
    for frame = 1:10
        for i = 1:count, materials(i).baseColor = colors(i, :); end
    end
    samples(k, 9) = toc(t) / 10 * 1e3;
    t = tic;
    for frame = 1:100, setParameters(materials, 'baseColor', colors); end
    samples(k, 10) = toc(t) / 100 * 1e3;
    t = tic;
    for i = 1:10
        r.render(scene, small);
        pixels = small.read(); %#ok<NASGU>
    end
    samples(k, 11) = toc(t) / 10 * 1e3;
    t = tic;
    for i = 1:5, pixels = large.read(); end %#ok<NASGU>
    samples(k, 12) = toc(t) / 5 * 1e3;
end
m = median(samples, 1);
results = struct('bareGatewayCall', m(1), 'gatewayGet', m(2), 'gatewaySet', m(3), 'classGet', m(4), ...
    'classSet', m(5), 'classMethod', m(6), 'transformsOneAtATimeMs', m(7), 'transformsBatchMs', m(8), ...
    'materialsOneAtATimeMs', m(9), 'materialsBatchMs', m(10), 'renderAndRead320x240Ms', m(11), ...
    'read1920x1080Ms', m(12), 'count', count);
close(r);
disp(results);
end
