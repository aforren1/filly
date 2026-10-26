% Textures from MATLAB arrays: H x W x C, first row at the top, as in the Python API.
r = filly.Renderer();
scene = r.createScene();
cam = r.createCamera();
scene.camera = cam;
w = 32;
h = 16;
aspect = w / h;
cam.setOrthographic('Left', -aspect, 'Right', aspect, 'Bottom', -1, 'Top', 1, 'Near', 0.1, 'Far', 10);
cam.position = [0 0 5];
cam.lookAt([0 0 0]);

% A quad that fills the view, with glTF UVs: (0, 0) is the top-left corner of the image.
positions = [-aspect -1 0; aspect -1 0; aspect 1 0; -aspect 1 0];
uvs = [0 1; 1 1; 1 0; 0 0];
quad = scene.createMesh(positions, [1 2 3; 1 3 4], 'Uvs', uvs, 'Unlit', true);
meshNodes = quad.nodes;
material = meshNodes(1).material();

% Distinct quadrants and a ramp, so that a flip, a transpose, or a channel swap shows.
pixels = zeros(h, w, 4, 'uint8');
pixels(1:h / 2, 1:w / 2, 1) = 255;
pixels(1:h / 2, w / 2 + 1:end, 2) = 255;
pixels(h / 2 + 1:end, 1:w / 2, 3) = 255;
pixels(:, :, 2) = pixels(:, :, 2) + uint8(repmat(0:w - 1, h, 1) * 4);
pixels(:, :, 4) = 255;
texture = r.createTexture(pixels, 'ColorSpace', 'srgb', 'Filter', 'nearest');
assert(texture.width == w && texture.height == h && texture.channels == 4);
assert(strcmp(texture.dtype, 'uint8') && strcmp(texture.colorSpace, 'srgb'));
material.baseColorTexture = texture;
assert(material.baseColorTexture == texture);
assert(isequal(material.baseColorTextureOffset, [0 0]));

target = r.createRenderTarget(w, h);
r.render(scene, target);
shown = target.read();
assert(isequal(shown, pixels), 'the rendered texture must match the array, first row at the top');

% update() replaces the pixels in place.
flipped = pixels(end:-1:1, :, :);
texture.update(flipped);
r.render(scene, target);
assert(isequal(target.read(), flipped));
expect_error(@() texture.update(pixels(:, :, 1:3)), 'filly:invalidArgument');
expect_error(@() texture.update(double(pixels)), 'filly:invalidArgument');

% One-channel sRGB textures expand to RGBA on upload; they update through a copy.
grey = uint8(repmat(0:8:248, h, 1));
greyTexture = r.createTexture(grey, 'ColorSpace', 'srgb', 'Filter', 'nearest');
material.baseColorTexture = greyTexture;
r.render(scene, target);
shown = target.read();
assert(isequal(shown(:, :, 1), grey) && isequal(shown(:, :, 3), grey));
greyTexture.update(grey(:, end:-1:1));
r.render(scene, target);
shown = target.read();
assert(isequal(shown(:, :, 2), grey(:, end:-1:1)));

% Float textures hold linear values.
linear = single(rand(h, w, 3));
floatTexture = r.createTexture(linear, 'ColorSpace', 'linear');
assert(strcmp(floatTexture.dtype, 'single') && floatTexture.channels == 3);
floatTexture.update(single(rand(h, w, 3)));

% Removing the texture restores the plain material.
material.baseColorTexture = [];
assert(isempty(material.baseColorTexture));
assert(isempty(material.baseColorTextureOffset));
expect_error(@() r.createTexture(pixels), 'filly:invalidArgument');

% An environment from an array: 2:1 linear RGB.
environment = r.createEnvironment(single(ones(32, 64, 3)));
scene.environment = environment;
assert(scene.environment == environment);
scene.environment = [];
