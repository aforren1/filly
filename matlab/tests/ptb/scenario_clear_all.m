% `clear all` with a window, a renderer, a PTBTarget, and a PTBTexture open. `clear all` also
% unloads PTB's Screen MEX file, which closes its windows, so MATLAB deletes the filly objects
% before or after the window closes, in an order that it chooses. Nothing may crash, and no
% gateway handle may remain.
addpath(fullfile(pwd, '..', '..'));
win = ptb_open('offscreen');
r = filly.Renderer.forWindow(win);
tgt = filly.PTBTarget(r, win);
tex = filly.PTBTexture(r, win, 32, 32);
scene = quad_scene(r, 640, 480);
r.render(scene, tgt);
tgt.draw();
Screen('Flip', win);
clear all %#ok<CLALL>
assert(mislocked('filly_mex'));
assert(filly_mex('handleCount') == 0);
sca;
% A new window and renderer work afterwards.
addpath(fullfile(pwd, '..', '..'));
win = ptb_open('plain');
r = filly.Renderer.forWindow(win);
tgt = filly.PTBTarget(r, win);
scene = quad_scene(r, 640, 480);
r.render(scene, tgt);
tgt.draw();
Screen('Flip', win);
close(r);
sca;
disp('SCENARIO OK');
