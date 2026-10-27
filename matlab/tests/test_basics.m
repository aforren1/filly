% Offscreen rendering, property round trips, read(), and image orientation.
filly.setLogLevel('warning');
r = filly.Renderer();
assert(~r.closed);
assert(ismember(r.glPlatform, {'wgl', 'glx', 'egl'}));
assert(r.sharedContext == 0);

scene = r.createScene();
cam = r.createCamera();
scene.camera = cam;
assert(scene.camera == cam);

% Scene properties keep what was set.
scene.background = [0.5 0.5 0.5 1];
assert(isequal(scene.background, [0.5 0.5 0.5 1]));
scene.toneMapping = 'aces_legacy';
assert(strcmp(scene.toneMapping, 'aces_legacy'));
scene.toneMapping = 'linear';
scene.msaa = 4;
assert(scene.msaa == 4);
scene.msaa = 1;
scene.fogColor = [1 0.5 0.25];
assert(max(abs(scene.fogColor - [1 0.5 0.25])) < 1e-6);
scene.environmentIntensity = single(12000);
assert(scene.environmentIntensity == 12000);
scene.encoding = 'srgb';
assert(strcmp(scene.encoding, 'srgb'));
assert(isempty(scene.environment));

% Camera properties.
cam.exposure = 12;
assert(abs(cam.exposure - 12) < 1e-4);
cam.exposure = 14.965784072875977;

% Three unlit quads at known places, in an orthographic view that matches the target aspect.
w = 64;
h = 48;
aspect = w / h;
cam.setOrthographic('Left', -aspect, 'Right', aspect, 'Bottom', -1, 'Top', 1, 'Near', 0.1, 'Far', 10);
cam.position = [0 0 5];
cam.lookAt([0 0 0]);
quad = @(x0, y0, x1, y1) [x0 y0 0; x1 y0 0; x1 y1 0; x0 y1 0];
faces = [1 2 3; 1 3 4];
red = scene.createMesh(quad(-aspect + 0.1, 0.2, -0.3, 0.9), faces, 'BaseColor', [1 0 0 1], 'Unlit', true);
green = scene.createMesh(quad(0.3, 0.2, aspect - 0.1, 0.9), faces, 'BaseColor', [0 1 0 1], 'Unlit', true);
blue = scene.createMesh(single(quad(-aspect + 0.1, -0.9, -0.3, -0.2)), int32(faces), 'BaseColor', [0 0 1 1], 'Unlit', true);
assert(red.isMesh && red.vertexCount == 4);

target = r.createRenderTarget(w, h);
assert(target.width == w && target.height == h);
r.render(scene, target);
img = target.read();
assert(isa(img, 'uint8') && isequal(size(img), [h w 4]));
pixel = @(img, fx, fy) double(squeeze(img(round(fy * h), round(fx * w), 1:3)))';
% The first row is the top of the image, as in the Python API.
assert(isequal(pixel(img, 0.2, 0.2), [255 0 0]), 'top left must be red');
assert(isequal(pixel(img, 0.8, 0.2), [0 255 0]), 'top right must be green');
assert(isequal(pixel(img, 0.2, 0.8), [0 0 255]), 'bottom left must be blue');
assert(isequal(pixel(img, 0.8, 0.8), [188 188 188]), 'bottom right must be the background');
assert(all(all(img(:, :, 4) == 255)));

% Hiding a model removes it from the image.
red.visible = false;
r.render(scene, target);
img = target.read();
assert(isequal(pixel(img, 0.2, 0.2), [188 188 188]));
red.visible = true;

% A viewport renders into part of the target; Clear false keeps the rest.
r.render(scene, target, 'Viewport', [0 0 w / 2 h / 2], 'Clear', false);

% The shapes match the Python module's arrays, with 1-based indices.
box = filly.shapes.box('Width', 2);
assert(size(box.positions, 2) == 3 && size(box.indices, 2) == 3);
assert(min(box.indices(:)) == 1 && max(box.indices(:)) == size(box.positions, 1));
assert(max(box.positions(:, 1)) == 1);
sphere = filly.shapes.uvSphere('Segments', 8, 'Rings', 4);
mesh = scene.createMesh(sphere.positions, sphere.indices, 'Normals', sphere.normals, 'Uvs', sphere.uvs);
assert(mesh.vertexCount == size(sphere.positions, 1));

% Stats are a struct.
stats = r.stats;
assert(stats.framesRendered >= 3);

% A model from a file, with nodes, names, and bounds: the bundled sample.
assert(isfile(filly.samples.horse()));
horse = scene.load(filly.samples.horse());
assert(numel(horse.nodes) >= 1);
assert(iscell(horse.nodeNames) && iscell(horse.materialNames));
bounds = horse.bounds;
assert(isequal(size(bounds), [2 3]) && all(bounds(2, :) >= bounds(1, :)));
distance = cam.frame(horse, 'Fill', 0.5);
assert(distance > 0);

% An asset adds instances to any scene of the renderer.
asset = r.loadAsset(filly.samples.horse());
second = r.createScene();
copy = second.add(asset);
assert(copy ~= horse);
assert(asset.memory.models == 1);

% Paths go to the core as UTF-8: a folder name outside the ANSI code page.
% The name is built from UTF-8 bytes: MATLAB stores char as UTF-16 and Octave as UTF-8.
name = native2unicode(uint8([195 188 110 105 99 111 100 101 45 230 181 139 232 175 149]), 'UTF-8');
folder = fullfile(tempdir, ['filly-' name]);
if ~isfolder(folder), mkdir(folder); end
copyfile(filly.samples.horse(), fullfile(folder, 'horse.glb'));
unicode = scene.load(fullfile(folder, 'horse.glb'));
assert(isequal(unicode.nodeNames, horse.nodeNames));
rmdir(folder, 's');

close(r);
assert(r.closed && scene.closed && target.closed);
