% Offscreen parity with gltf_viewer: the references and the tolerance of the Python harness.
%
% tools/compare_reference.py writes gltf_viewer's image (viewer.png), filly's Python image
% (filly.png), and the camera (camera.json) for each asset. This test renders the same assets
% with the same settings from MATLAB, one renderer per asset as the harness does, and checks
% against both images the tolerance that docs/how-to/reference-comparison.md records for these
% assets: largest channel difference at most 2, and no pixel over 2.
%
% On Intel Iris Xe, MetalRoughSpheresNoTextures shows 0 to 3 pixels per frame at random places
% that differ by up to 120 levels, in both frontends (matlab/PLAN.md, phase 2 findings). As the
% harness does for gltf_viewer captures, the test uses the per-pixel median of three frames.
%
% FILLY_REFERENCE_DIR names the harness output (default .deps/reference-compare). The test is
% skipped without a report there. Assets that the how-to records as intended divergences are
% left out.
filly.setLogLevel('warning');
root = fileparts(fileparts(fileparts(mfilename('fullpath'))));
folder = getenv('FILLY_REFERENCE_DIR');
if isempty(folder)
    folder = fullfile(root, '.deps', 'reference-compare');
end
report = fullfile(folder, 'report-env-studio.json');
if ~isfile(report)
    fprintf('SKIP test_parity: no %s; run tools/compare_reference.py first\n', report);
    return
end
data = jsondecode(fileread(report));
config = data.config;
divergent = {'MosquitoInAmber', 'EmissiveStrengthTest', 'CompareEmissiveStrength', 'IridescenceSuzanne', ...
    'suzanne-source', 'IridescenceDielectricSpheres', 'IridescenceMetallicSpheres', 'AnisotropyStrengthTest', ...
    'DiffuseTransmissionTest'};
assert(strcmp(config.antialiasing, 'none') && config.msaa == 1 && ~config.sun && config.skybox, ...
    'the test reproduces the harness default configuration only');
sizes = sscanf(lower(config.size), '%dx%d');
width = sizes(1);
height = sizes(2);
metricsOf = @(a, b) struct('mae', mean(abs(double(a(:)) - double(b(:)))), ...
    'maxError', max(abs(double(a(:)) - double(b(:)))), ...
    'fracOver2', mean(reshape(max(abs(double(a) - double(b)), [], 3), [], 1) > 2), ...
    'differing', nnz(max(abs(double(a) - double(b)), [], 3)));

results = data.results;
if isstruct(results), results = num2cell(results); end
% A report from before a folder move names files that no longer exist. That is a stale
% harness run, not a parity failure.
paths = [{config.environment}; cellfun(@(e) e.path, results(:), 'UniformOutput', false)];
stale = paths(~cellfun(@isfile, paths));
if ~isempty(stale)
    fprintf('SKIP test_parity: %s names %d missing files (first: %s); rerun tools/compare_reference.py\n', ...
        report, numel(stale), stale{1});
    return
end
checked = 0;
for i = 1:numel(results)
    entry = results{i};
    if ~strcmp(entry.status, 'ok') || any(strcmp(entry.asset, divergent))
        continue
    end
    directory = fullfile(folder, 'env-studio', entry.asset);
    camera = jsondecode(fileread(fullfile(directory, 'camera.json')));
    % As render_filly() in tools/compare_reference.py.
    r = filly.Renderer();
    target = r.createRenderTarget(width, height);
    scene = r.createScene();
    scene.background = [0 0 0 1];
    scene.shadows = false;
    scene.antialiasing = config.antialiasing;
    scene.msaa = config.msaa;
    scene.toneMapping = 'aces_legacy';
    scene.encoding = 'srgb';
    scene.load(entry.path);
    cam = r.createCamera();
    cam.setLensProjection('FocalLengthMm', camera.focal_length_mm, 'Aspect', width / height, ...
        'Near', camera.near, 'Far', camera.far);
    cam.position = camera.eye(:)';
    cam.lookAt(camera.target(:)', camera.up(:)');
    scene.environment = r.loadEnvironment(config.environment);
    scene.environmentIntensity = 30000;
    scene.environmentVisible = config.skybox;
    scene.camera = cam;
    for frame = 1:config.frames
        r.render(scene, target);
    end
    frames = zeros(height, width, 3, 3, 'uint8');
    for k = 1:3
        if k > 1, r.render(scene, target); end
        pixels = target.read();
        frames(:, :, :, k) = pixels(:, :, 1:3);
    end
    pixels = median(frames, 4);
    close(r);

    viewer = metricsOf(pixels, imread(fullfile(directory, 'viewer.png')));
    python = metricsOf(pixels, imread(fullfile(directory, 'filly.png')));
    fprintf('  %-28s viewer: MAE %.4f max %d frac>2 %.5f | Python: max %d, %d pixels differ\n', entry.asset, ...
        viewer.mae, viewer.maxError, viewer.fracOver2, python.maxError, python.differing);
    assert(viewer.maxError <= 2 && viewer.fracOver2 == 0, 'filly:test', '%s exceeds the parity tolerance', entry.asset);
    assert(python.maxError <= 2 && python.fracOver2 == 0, 'filly:test', '%s differs from the Python frontend', entry.asset);
    checked = checked + 1;
end
assert(checked > 0, 'filly:test', 'the report lists no asset to check');
fprintf('  %d assets within the tolerance against gltf_viewer and the Python frontend\n', checked);
