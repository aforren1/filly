function [win, stereo] = ptb_open(config)
% PTB_OPEN  Open a 640 x 480 Psychtoolbox window in one of the configurations of phase 0.
%   config: 'plain', 'imaging', 'msaa4', 'float32', 'stereo4', 'offscreen' (imaging pipeline
%   with fast offscreen windows, for PTB-drawn textures).
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
    case 'offscreen'
        PsychImaging('PrepareConfiguration');
        PsychImaging('AddTask', 'General', 'UseFastOffscreenWindows');
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
    otherwise
        error('filly:test', 'Unknown configuration %s', config);
end
end
