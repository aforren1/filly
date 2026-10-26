% `clear mex` (in Octave, `clear functions`) must not unload the gateway while a renderer
% exists. It unloads PTB's Screen MEX file, which closes the window. On Windows, the renderer
% then still works offscreen, the target reports that its host context is gone, and close()
% makes no host OpenGL calls. On Linux, Screen also closes its X display connection, which
% Filament's shared GLX context uses, so the renderer closes itself first.
addpath(fullfile(pwd, '..', '..'), fullfile(pwd, '..'));
win = ptb_open('plain');
r = filly.Renderer.forWindow(win);
tgt = filly.PTBTarget(r, win);
scene = quad_scene(r, 640, 480);
r.render(scene, tgt);
tgt.draw();
Screen('Flip', win);
if exist('OCTAVE_VERSION', 'builtin')
    % Octave reads `clear mex` as a variable name. `clear functions` unloads MEX files too.
    clear functions
else
    clear mex %#ok<CLMEX>
end
assert(mislocked('filly_mex'));
if ispc
    assert(~r.closed && ~tgt.closed);
    offscreen = r.createRenderTarget(64, 48);
    r.render(scene, offscreen);
    assert(isequal(size(offscreen.read()), [48 64 4]));
    expect_error(@() r.render(scene, tgt), 'filly:interop');
else
    assert(r.closed && tgt.closed, 'the renderer must close with the X display connection');
    expect_error(@() r.render(scene, tgt), 'filly:error');
end
close(r);
assert(tgt.closed);
sca;
disp('SCENARIO OK');
