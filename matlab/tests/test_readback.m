% Pipelined readbacks with beginRead(): the same pixels as read(), and the close rules.
filly.setLogLevel('warning');
r = filly.Renderer();
scene = r.createScene();
cam = r.createCamera();
scene.camera = cam;
w = 48;
h = 32;
aspect = w / h;
cam.setOrthographic('Left', -aspect, 'Right', aspect, 'Bottom', -1, 'Top', 1, 'Near', 0.1, 'Far', 10);
cam.position = [0 0 5];
cam.lookAt([0 0 0]);
quad = [-0.25 -0.25 0; 0.25 -0.25 0; 0.25 0.25 0; -0.25 0.25 0];
square = scene.createMesh(quad, [1 2 3; 1 3 4], 'BaseColor', [1 0 0 1], 'Unlit', true);
target = r.createRenderTarget(w, h);
color = square.material('mesh');

expect_error(@() target.beginRead(), 'filly:error');  % no render yet

% Frames that differ: the square moves and changes color.
frames = 6;
expected = cell(1, frames);
for k = 1:frames
    square.position = [-0.8 + 0.25 * k, 0.3, 0];
    color.baseColor = [mod(k * 37, 256) / 255, mod(k * 91, 256) / 255, 0.5, 1];
    r.render(scene, target);
    expected{k} = target.read();
end
% The top row is the top of the image, as in read().
assert(isequal(double(squeeze(expected{1}(1, 1, 1:3)))', [0 0 0]));
for k = 2:frames
    assert(~isequal(expected{k}, expected{k - 1}), 'frames must differ');
end

% One frame in flight: read frame k - 1 after the render of frame k.
pending = [];
got = {};
for k = 1:frames
    square.position = [-0.8 + 0.25 * k, 0.3, 0];
    color.baseColor = [mod(k * 37, 256) / 255, mod(k * 91, 256) / 255, 0.5, 1];
    r.render(scene, target);
    readback = target.beginRead();
    assert(readback.frameNumber == r.stats.framesRendered);
    if ~isempty(pending)
        got{end + 1} = pending.read(); %#ok<SAGROW>
        assert(pending.closed);
    end
    pending = readback;
end
deadline = tic;
while ~pending.ready()
    assert(toc(deadline) < 5, 'ready() must become true');
    pause(0.001);
end
got{end + 1} = pending.read();
for k = 1:frames
    assert(isequal(got{k}, expected{k}), sprintf('frame %d differs from read()', k));
end
assert(r.stats.readbackMs > 0);

% Close rules: once read or closed, a readback raises; closing the target closes it.
expect_error(@() pending.read(), 'filly:error');  % already read
pending.close();
inFlight = target.beginRead();
target.close();
assert(inFlight.closed);
expect_error(@() inFlight.read(), 'filly:error');  % target closed
r.close();
