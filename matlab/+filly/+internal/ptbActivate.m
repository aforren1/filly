function ptbActivate(window)
% PTBACTIVATE  Make the OpenGL context of a Psychtoolbox onscreen window current.
%   filly's host calls (renderer creation, acquire, release, writes, close) place fences in the
%   current context, and Screen('DrawTexture') runs in PTB's own context, so both must be the
%   same. Psychtoolbox raises an error inside Screen('BeginOpenGL'), where a userspace context
%   can be current instead.
Screen('GetWindowInfo', window, -1);
end
