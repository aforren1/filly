% `sca` first, then `clear all`: the objects close without host OpenGL calls.
addpath(fullfile(pwd, '..', '..'));
win = ptb_open('plain');
r = filly.Renderer.forWindow(win);
tgt = filly.PTBTarget(r, win);
scene = quad_scene(r, 640, 480);
r.render(scene, tgt);
tgt.draw();
Screen('Flip', win);
sca;
clear all %#ok<CLALL>
assert(filly_mex('handleCount') == 0);
% A new window and renderer work after the old ones are gone.
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
