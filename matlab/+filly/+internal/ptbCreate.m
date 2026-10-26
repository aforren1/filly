function [id, glTexture] = ptbCreate(renderer, window, width, height, import)
% PTBCREATE  An immutable GL_RGBA8 texture in a PTB window's context, imported into filly.
%   import(glTexture) returns the gateway handle of the imported target or texture.
filly.internal.ptbActivate(window);
if filly_mex('currentContext') ~= renderer.sharedContext
    error('filly:interop', ['The renderer does not share this window''s OpenGL context. ' ...
        'Create it with filly.Renderer.forWindow(window).']);
end
glTexture = filly_mex('createHostTexture', width, height);
try
    id = import(glTexture);
catch err
    filly_mex('deleteHostTexture', glTexture);
    rethrow(err);
end
end
