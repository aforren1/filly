% MATLAB exits with the window, the renderer, and the target still open: the gateway's exit
% handler and PTB's own shutdown run in whichever order MATLAB chooses.
addpath(fullfile(pwd, '..', '..'));
win = ptb_open('plain');
r = filly.Renderer.forWindow(win);
tgt = filly.PTBTarget(r, win);
scene = quad_scene(r, 640, 480);
r.render(scene, tgt);
tgt.draw();
Screen('Flip', win);
disp('SCENARIO OK');
