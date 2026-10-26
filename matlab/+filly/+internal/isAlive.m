function tf = isAlive(object)
% ISALIVE  Whether a handle object was not deleted, for example by `clear all`.
%   Octave has no isvalid. Its deleted handle objects stay usable, and a deleted filly.Renderer
%   has an empty HostTextures list, so true is safe there.
if exist('OCTAVE_VERSION', 'builtin')
    tf = ~isempty(object);
else
    tf = isvalid(object);
end
end
