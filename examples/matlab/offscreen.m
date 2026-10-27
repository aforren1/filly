function pixels = offscreen(output)
%OFFSCREEN Render the sample horse offscreen and save it as a PNG file.
%   pixels = offscreen() renders a 960 x 640 image, writes it to horse.png in the current
%   folder, and returns it as a 640 x 960 x 4 uint8 array. offscreen(output) writes to output.
%
%   Needs the matlab folder on the path (SetupFilly). Runs in MATLAB and Octave.
%   The horse is the bundled sample, "Horse" by Quaternius (CC0 1.0).
if nargin < 1, output = 'horse.png'; end

r = filly.Renderer();
% onCleanup closes the renderer and its GPU resources also after an error.
cleanup = onCleanup(@() close(r));

scene = r.createScene();
scene.background = [0.025 0.035 0.055 1];
scene.antialiasing = 'fxaa';
scene.shadows = true;
horse = scene.load(filly.samples.horse());
horse.applyAnimation(1, 0.5);   % mid-gallop

cam = r.createCamera();
scene.camera = cam;
% A three-quarter view: 40 degrees of yaw, 12 degrees of pitch.
yaw = deg2rad(40);
pitch = deg2rad(12);
direction = -[sin(yaw) * cos(pitch), sin(pitch), cos(yaw) * cos(pitch)];
cam.frame(horse, 'Fill', 1, 'Direction', direction, 'Fit', 'box');

key = scene.addDirectionalLight([-1 -1.5 -1], 'Intensity', 110000, 'Color', [1 0.92 0.82]);
key.castsShadows = true;
scene.addDirectionalLight([1 -0.3 -1], 'Intensity', 30000, 'Color', [0.55 0.75 1]);
scene.environment = r.createEnvironment(studioEnvironment());
scene.environmentIntensity = 20000;

target = r.createRenderTarget(960, 640);
r.render(scene, target);
pixels = target.read();   % first row at the top
imwrite(pixels(:, :, 1:3), output);
fprintf('Wrote %s\n', output);
end

function panorama = studioEnvironment()
% A small linear HDR panorama (H x 2H x 3) with two softboxes in a dim room.
panorama = repmat(single(0.12), 64, 128, 3);
panorama(9:30, 13:28, :) = repmat(reshape(single([3.0 2.8 2.5]), 1, 1, 3), 22, 16);
panorama(13:40, 85:100, :) = repmat(reshape(single([1.5 1.8 2.2]), 1, 1, 3), 28, 16);
end
