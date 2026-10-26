classdef PTBTexture < filly.ImportedTexture
    % PTBTEXTURE  A Psychtoolbox texture that PTB draws into and filly materials sample.
    %
    %   tex = filly.PTBTexture(r, win, width, height);           % 'ColorSpace', 'srgb'
    %   tex.beginWrite();
    %   Screen('FillRect', tex.texture, [255 0 0], [0 0 64 64]);  % any Screen drawing
    %   tex.endWrite();
    %   material.baseColorTexture = tex;
    %   r.render(scene, tgt);
    %
    %   The texture is an immutable GL_TEXTURE_2D with GL_RGBA8 storage that filly creates in
    %   the window's context and wraps with Screen('SetOpenGLTexture'). Drawing into a PTB
    %   texture needs the imaging pipeline, for example
    %   PsychImaging('AddTask', 'General', 'UseFastOffscreenWindows'). Option 'ColorSpace'
    %   ('srgb'): PTB writes display values, which 'srgb' decodes to linear light;
    %   'linear' samples them unchanged. Options 'Filter' ('linear'), 'Wrap' ('repeat').
    %
    %   Ownership and shutdown follow filly.PTBTarget.

    properties (SetAccess = private)
        window = []
        % The PTB texture handle, which Screen drawing commands take as their window.
        texture = []
        glTexture = 0
    end

    methods
        function obj = PTBTexture(renderer, window, width, height, varargin)
            o = filly.internal.options(struct('ColorSpace', 'srgb', 'Filter', 'linear', 'Wrap', 'repeat'), varargin);
            [id, glTexture] = filly.internal.ptbCreate(renderer, window, width, height, ...
                @(gl) filly_mex('Renderer.importGlTexture', renderer.Id, gl, width, height, ...
                filly.internal.text(o.ColorSpace), filly.internal.text(o.Filter), filly.internal.text(o.Wrap)));
            obj@filly.ImportedTexture(renderer, id);
            obj.window = window;
            obj.glTexture = glTexture;
            try
                obj.texture = Screen('SetOpenGLTexture', window, [], glTexture, 3553, width, height, 32, [], 32);
            catch err
                filly_mex('ImportedTexture.close', id);
                filly_mex('deleteHostTexture', glTexture);
                obj.window = [];
                rethrow(err);
            end
            renderer.HostTextures(end + 1, :) = [window, obj.texture, glTexture];
        end

        function delete(obj)
            if ~isempty(obj.window)
                try
                    close(obj);
                catch
                end
            end
        end

        function beginWrite(obj)
            filly.internal.ptbActivate(obj.window);
            filly_mex('ImportedTexture.beginWrite', obj.Id);
        end

        function endWrite(obj)
            % PTB draws into the texture in the window's context, so the fence goes there.
            filly.internal.ptbActivate(obj.window);
            filly_mex('ImportedTexture.endWrite', obj.Id);
        end

        function close(obj)
            % Remove the texture from all materials, then release the PTB texture.
            if isempty(obj.window)
                return
            end
            window = obj.window;
            obj.window = [];
            row = [];
            if filly.internal.isAlive(obj.Owner)
                hosts = obj.Owner.HostTextures;
                row = find(hosts(:, 1) == window & hosts(:, 2) == obj.texture, 1);
            end
            if filly.internal.ptbHostOpen(window, obj.texture, obj.glTexture)
                filly.internal.ptbActivate(window);
            end
            try
                filly_mex('ImportedTexture.close', obj.Id);
            catch err
                obj.forget(row, window);
                rethrow(err);
            end
            obj.forget(row, window);
        end
    end

    methods (Access = private)
        function forget(obj, row, window)
            if isempty(row)
                return
            end
            obj.Owner.HostTextures(row, :) = [];
            filly.internal.ptbRelease([window, obj.texture, obj.glTexture]);
        end
    end
end
