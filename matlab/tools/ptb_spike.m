function result = ptb_spike(config, logFile)
% PTB_SPIKE  Phase 0 of matlab/PLAN.md: filly interop with one Psychtoolbox window.
%   result = ptb_spike(config, logFile) opens a PTB window in the given configuration, renders
%   into a filly ImportedTarget wrapped with Screen('SetOpenGLTexture'), and records contexts,
%   orientation, pixel parity, synchronization under a slow frame, and per-frame costs. It
%   calls filly_mex directly, without the +filly classes, so that it tests the gateway and the
%   core only. Results go to the struct and, as JSON, to logFile.
%
%   config: 'plain', 'imaging', 'msaa4', 'float32', 'stereo4', 'stereo1', '3d'.
%
%   Run one configuration per MATLAB process: a driver failure can leave the process unusable.

if nargin < 2, logFile = ''; end
result = struct('config', config, 'ok', false, 'error', '', 'gl', '', 'contexts', struct(), ...
    'orientation', '', 'parityMaxDiff', struct(), 'parityPixelsOver2', struct(), 'slow', struct(), 'control', struct(), ...
    'timingMs', struct(), 'insideBeginOpenGL', '', 'shutdown', '');
GL_TEXTURE_2D = 3553;
win = [];
r = [];
try
    filly_mex('setLogLevel', 'warning');
    Screen('Preference', 'SkipSyncTests', 2);
    Screen('Preference', 'Verbosity', 1);
    Screen('Preference', 'VisualDebugLevel', 0);
    screen = max(Screen('Screens'));
    rect = [0 0 640 480];
    stereo = 0;
    switch config
        case 'plain'
            win = Screen('OpenWindow', screen, 0, rect);
        case 'imaging'
            PsychImaging('PrepareConfiguration');
            PsychImaging('AddTask', 'General', 'UseVirtualFramebuffer');
            win = PsychImaging('OpenWindow', screen, 0, rect);
        case 'msaa4'
            win = Screen('OpenWindow', screen, 0, rect, [], [], [], 4);
        case 'float32'
            PsychImaging('PrepareConfiguration');
            PsychImaging('AddTask', 'General', 'FloatingPoint32Bit');
            win = PsychImaging('OpenWindow', screen, 0, rect);
        case 'stereo4'
            stereo = 4;
            PsychImaging('PrepareConfiguration');
            win = PsychImaging('OpenWindow', screen, 0, [0 0 1280 480], [], [], 4);
        case 'stereo1'
            stereo = 1;
            win = Screen('OpenWindow', screen, 0, rect, [], [], 1);
        case '3d'
            % A userspace OpenGL context for BeginOpenGL, as 3D PTB code sets up.
            InitializeMatlabOpenGL(0, 0);
            win = Screen('OpenWindow', screen, 0, rect);
        otherwise
            error('Unknown configuration %s', config);
    end
    info = Screen('GetWindowInfo', win);
    result.gl = sprintf('%s | %s | imaging %d', info.GLRenderer, info.GLVersion, info.ImagingMode);
    % With stereo mode 4, WindowSize already reports one eye's half of the window.
    [w, h] = Screen('WindowSize', win);

    % --- contexts
    ctx.afterOpen = filly_mex('currentContext');
    Screen('GetWindowInfo', win, -1);
    ctx.afterActivate = filly_mex('currentContext');
    % BeginOpenGL needs InitializeMatlabOpenGL before the window opens (the '3d' configuration).
    has3d = begin_gl(win, 0);
    if has3d
        ctx.insideBeginOpenGL = filly_mex('currentContext');
        Screen('EndOpenGL', win);
        ctx.afterEndOpenGL = filly_mex('currentContext');
        begin_gl(win, 1);
        ctx.insideBeginOpenGLShared = filly_mex('currentContext');
        Screen('EndOpenGL', win);
    end
    Screen('Flip', win);
    ctx.afterFlip = filly_mex('currentContext');

    % --- renderer and target in PTB's context
    Screen('GetWindowInfo', win, -1);
    host = filly_mex('currentContext');
    r = filly_mex('Renderer.new', host);
    ctx.afterRenderer = filly_mex('currentContext');
    result.contexts = structfun(@(v) sprintf('0x%x', v), ctx, 'UniformOutput', false);
    gl = filly_mex('createHostTexture', w, h);
    tgt = filly_mex('Renderer.importGlTarget', r, gl, w, h);
    ptbTexture = Screen('SetOpenGLTexture', win, [], gl, GL_TEXTURE_2D, w, h, 32, [], 32);
    offscreen = filly_mex('Renderer.createRenderTarget', r, w, h);

    % --- a known image: red top left, green top right, blue bottom left, on grey
    [scene, cam] = quad_scene(r, w, h);
    filly_mex('Renderer.render', r, scene, tgt);
    filly_mex('Renderer.render', r, scene, offscreen);
    expected = filly_mex('RenderTarget.read', offscreen);
    % DrawTexture's default bilinear filter, nearest-neighbor (filterMode 0), and bilinear
    % with a source rectangle that undoes the half-texel inset that PTB applies to
    % GL_TEXTURE_2D textures (filterMode 2 here, mapped to 1 in draw_and_capture).
    for filterMode = [1 0 2]
        shown = draw_and_capture(win, tgt, ptbTexture, stereo, w, h, true, filterMode);
        Screen('Flip', win);
        difference = max(abs(double(shown) - double(expected(:,:,1:3))), [], 3);
        field = sprintf('filter%d', filterMode);
        result.parityMaxDiff.(field) = max(difference(:));
        result.parityPixelsOver2.(field) = nnz(difference > 2);
        if ~isempty(logFile)
            [folder, stem] = fileparts(logFile);
            imwrite(shown, fullfile(folder, sprintf('%s-ptb-%s.png', stem, field)));
        end
    end
    result.orientation = orientation(shown);
    if ~isempty(logFile)
        imwrite(expected(:,:,1:3), fullfile(folder, [stem '-offscreen.png']));
    end

    % --- filly calls inside BeginOpenGL
    if has3d
        Screen('BeginOpenGL', win);
        try
            filly_mex('ImportedTarget.acquire', tgt);
            filly_mex('ImportedTarget.release', tgt);
            result.insideBeginOpenGL = 'accepted';
        catch err
            result.insideBeginOpenGL = [err.identifier ': ' err.message];
        end
        Screen('EndOpenGL', win);
    else
        result.insideBeginOpenGL = 'BeginOpenGL unavailable without InitializeMatlabOpenGL';
    end

    % --- slow frames, with and without synchronization
    [heavy, heavyMaterials] = heavy_scene(r, cam);
    result.slow = sync_test(r, win, heavy, heavyMaterials, tgt, ptbTexture, stereo, w, h, true, 40);
    result.control = sync_test(r, win, heavy, heavyMaterials, tgt, ptbTexture, stereo, w, h, false, 40);

    % --- per-frame cost in the light scene
    result.timingMs = timing(r, win, scene, tgt, ptbTexture, stereo, has3d, 300);

    % --- shutdown: the window first, as with `sca`, then the renderer
    sca;
    win = [];
    filly_mex('Renderer.close', r);
    r = [];
    result.shutdown = 'sca, then Renderer.close: no error';
    result.ok = true;
catch err
    result.error = getReport(err, 'extended', 'hyperlinks', 'off');
    if ~isempty(r)
        try, filly_mex('Renderer.close', r); catch, end
    end
    sca;
end
if ~isempty(logFile)
    fid = fopen(logFile, 'w');
    fprintf(fid, '%s', jsonencode(result, 'PrettyPrint', true));
    fclose(fid);
end
end

function [scene, cam] = quad_scene(r, w, h)
scene = filly_mex('Renderer.createScene', r);
cam = filly_mex('Renderer.createCamera', r);
filly_mex('set', scene, 'camera', cam);
filly_mex('set', scene, 'background', [0.5 0.5 0.5 1]);
aspect = w / h;
filly_mex('Camera.setOrthographic', cam, -aspect, aspect, -1, 1, 0.1, 10);
filly_mex('set', cam, 'position', [0 0 5]);
filly_mex('Camera.lookAt', cam, [0 0 0]);
quad(scene, [-aspect + 0.1, 0.2, -0.3, 0.9], [1 0 0 1]);
quad(scene, [0.3, 0.2, aspect - 0.1, 0.9], [0 1 0 1]);
quad(scene, [-aspect + 0.1, -0.9, -0.3, -0.2], [0 0 1 1]);
end

function quad(scene, box, color)
positions = [box(1) box(2) 0; box(3) box(2) 0; box(3) box(4) 0; box(1) box(4) 0];
filly_mex('Scene.createMesh', scene, positions, [1 2 3; 1 3 4], [], [], [], color, 0, 1, [0 0 0], true);
end

function [scene, materials] = heavy_scene(r, cam)
% Enough geometry and MSAA that one frame takes tens of milliseconds on an integrated GPU.
scene = filly_mex('Renderer.createScene', r);
filly_mex('set', scene, 'camera', cam);
filly_mex('set', scene, 'msaa', 4);
filly_mex('set', scene, 'ssao', true);
filly_mex('set', scene, 'background', [0 0 0 1]);
sphere = filly_mex('shape', 'uvSphere', 0.5, 512, 256);
count = 24;
materials = zeros(1, count, 'uint64');
for i = 1:count
    m = filly_mex('Scene.createMesh', scene, sphere.positions, sphere.indices, sphere.normals, [], [], ...
        [1 0 0 1], 0, 1, [0 0 0], true);
    nodes = filly_mex('get', m, 'nodes');
    materials(i) = filly_mex('Node.material', nodes(1), 1);
end
end

function shown = draw_and_capture(win, tgt, ptbTexture, stereo, w, h, synchronized, filterMode)
if nargin < 8, filterMode = 1; end
source = [];
destination = [];
if filterMode == 2
    filterMode = 1;
    source = [0 -0.5 w + 0.5 h];
    destination = [0 0 w h];
end
if synchronized, filly_mex('ImportedTarget.acquire', tgt); end
if stereo
    for eye = 0:1
        Screen('SelectStereoDrawBuffer', win, eye);
        Screen('DrawTexture', win, ptbTexture, source, destination, [], filterMode);
    end
    Screen('SelectStereoDrawBuffer', win, 0);
else
    Screen('DrawTexture', win, ptbTexture, source, destination, [], filterMode);
end
if synchronized, filly_mex('ImportedTarget.release', tgt); end
% With stereo mode 4 the drawing buffer of the left eye is w x h.
shown = Screen('GetImage', win, [0 0 w h], 'drawBuffer');
end

function text = orientation(img)
[h, w, ~] = size(img);
probe = @(fx, fy) double(squeeze(img(round(fy * h), round(fx * w), :)))';
tl = probe(0.2, 0.2); tr = probe(0.8, 0.2); bl = probe(0.2, 0.8); br = probe(0.8, 0.8);
names = {'red', 'green', 'blue', 'grey'};
colors = [255 0 0; 0 255 0; 0 0 255; 188 188 188];
name = @(c) names{find(sum(abs(colors - c), 2) <= 6, 1)};
try
    text = sprintf('top left %s, top right %s, bottom left %s, bottom right %s', name(tl), name(tr), name(bl), name(br));
catch
    text = sprintf('unexpected colors: tl %s tr %s bl %s br %s', mat2str(tl), mat2str(tr), mat2str(bl), mat2str(br));
end
end

function out = sync_test(r, win, scene, materials, tgt, ptbTexture, stereo, w, h, synchronized, frames)
% Alternates the spheres between red and green. A draw that does not wait for Filament shows the
% previous frame's color or a partial frame.
colors = [1 0 0 1; 0 1 0 1];
wrong = 0;
gpu = [];
for f = 1:frames
    k = mod(f, 2) + 1;
    filly_mex('Material.setParameters', materials, 'baseColor', repmat(colors(k, :), numel(materials), 1));
    filly_mex('Renderer.render', r, scene, tgt);
    shown = draw_and_capture(win, tgt, ptbTexture, stereo, w, h, synchronized);
    Screen('Flip', win);
    center = double(squeeze(shown(round(h / 2), round(w / 2), :)))';
    if any(abs(center - 255 * colors(k, 1:3)) > 2), wrong = wrong + 1; end
    stats = filly_mex('get', r, 'stats');
    if ~isempty(stats.gpuFrameMs), gpu(end + 1) = stats.gpuFrameMs; end %#ok<AGROW>
end
out = struct('frames', frames, 'wrongFrames', wrong, 'gpuFrameMsMedian', median(gpu));
end

function out = timing(r, win, scene, tgt, ptbTexture, stereo, has3d, frames)
t = zeros(frames, 6);
for f = 1:frames
    filly_mex('Renderer.render', r, scene, tgt);
    a = GetSecs;
    Screen('GetWindowInfo', win, -1);
    b = GetSecs;
    filly_mex('ImportedTarget.acquire', tgt);
    c = GetSecs;
    if stereo
        Screen('SelectStereoDrawBuffer', win, 0);
    end
    Screen('DrawTexture', win, ptbTexture);
    d = GetSecs;
    filly_mex('ImportedTarget.release', tgt);
    e = GetSecs;
    if has3d
        Screen('BeginOpenGL', win);
        Screen('EndOpenGL', win);
    end
    g = GetSecs;
    Screen('Flip', win);
    t(f, :) = [b - a, c - b, d - c, e - d, g - e, 0];
end
m = 1000 * median(t(11:end, :), 1);
if ~has3d, m(5) = NaN; end
out = struct('activateContext', m(1), 'acquire', m(2), 'drawTexture', m(3), 'release', m(4), ...
    'beginEndOpenGLPair', m(5));
end

function ok = begin_gl(win, share)
% A failed Screen call closes PTB's windows, so the preference is checked instead of trying.
ok = Screen('Preference', 'Enable3DGraphics') > 0;
if ok, Screen('BeginOpenGL', win, share); end
end
