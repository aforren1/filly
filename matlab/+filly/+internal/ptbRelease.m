function ptbRelease(rows)
% PTBRELEASE  Close the PTB textures of filly targets and textures after Filament released them.
%   rows: [window ptbTexture glTexture] per texture. PTB deletes the OpenGL texture with its
%   texture record. Without the window, there is nothing to release: the context took the
%   texture with it.
for i = 1:size(rows, 1)
    if filly.internal.ptbHostOpen(rows(i, 1), rows(i, 2), rows(i, 3))
        Screen('Close', rows(i, 2));
    end
end
end
