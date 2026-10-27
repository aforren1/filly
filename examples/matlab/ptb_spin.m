function ptb_spin(screenNumber)
%PTB_SPIN Show the sample horse galloping and turning in a Psychtoolbox window until a key press.
%   ptb_spin() opens a window on the highest-numbered screen. ptb_spin(screenNumber) opens it
%   on that screen.
%
%   Needs Psychtoolbox and the matlab folder on the path (SetupFilly). filly renders into a PTB
%   texture through the window's shared OpenGL context; see docs/how-to/matlab.md for the rules.
%   The horse is the bundled sample, "Horse" by Quaternius (CC0 1.0).
if nargin < 1, screenNumber = max(Screen('Screens')); end

win = Screen('OpenWindow', screenNumber, 0);
try
    r = filly.Renderer.forWindow(win);
    tgt = filly.PTBTarget(r, win);   % a PTB texture that covers the window

    scene = r.createScene();
    scene.antialiasing = 'fxaa';
    horse = scene.load(filly.samples.horse());
    cam = r.createCamera();
    scene.camera = cam;
    % The sphere fit keeps the whole horse in view at every rotation.
    cam.frame(horse, 'Direction', [0 -0.2 -1], 'Fit', 'sphere');
    scene.addDirectionalLight([-1 -1.5 -1], 'Intensity', 110000, 'Color', [1 0.92 0.82]);
    scene.environment = r.createEnvironment(studioEnvironment());
    scene.environmentIntensity = 20000;
    scene.environmentVisible = true;   % the environment is the background

    % The horse's origin is not its center. Turn it about the vertical axis through the center,
    % so that it stays in the framed sphere.
    bounds = horse.bounds;             % [min; max]
    center = mean(bounds, 1);

    start = GetSecs;
    while ~KbCheck
        t = GetSecs - start;
        horse.applyAnimation(1, t);       % the gallop loops
        horse.rotationEulerDeg = [0 30 * t 0];   % 30 degrees per second
        R = horse.transform(1:3, 1:3);
        horse.position = center - (R * center')';
        % filly calls go outside Screen('BeginOpenGL').
        r.render(scene, tgt);
        tgt.draw();
        Screen('Flip', win);
    end
    close(r);   % also closes tgt; the window must close last
catch err
    sca;
    rethrow(err);
end
sca;
end

function panorama = studioEnvironment()
% A small linear HDR panorama (H x 2H x 3) with two softboxes in a dim room.
panorama = repmat(single(0.12), 64, 128, 3);
panorama(9:30, 13:28, :) = repmat(reshape(single([3.0 2.8 2.5]), 1, 1, 3), 22, 16);
panorama(13:40, 85:100, :) = repmat(reshape(single([1.5 1.8 2.2]), 1, 1, 3), 28, 16);
end
