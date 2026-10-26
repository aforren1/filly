function tf = ptbHostOpen(window, texture, glTexture)
% PTBHOSTOPEN  Whether a PTB window and the PTB texture that filly wrapped in it still exist.
%   After `sca`, PTB can give the same handles to new windows and textures, so the texture's
%   OpenGL name must match too. Checks run in an order that cannot raise an error: a failed
%   Screen call can close PTB's windows.
tf = false;
if ~filly.internal.ptbAvailable()
    return
end
open = Screen('Windows');
if ~any(open == window) || ~any(open == texture)
    return
end
if Screen('WindowKind', window) ~= 1 || Screen('WindowKind', texture) ~= -1
    return
end
tf = Screen('GetOpenGLTexture', window, texture) == glTexture;
end
