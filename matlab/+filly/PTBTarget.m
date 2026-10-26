classdef PTBTarget < filly.ImportedTarget
    % PTBTARGET  A Psychtoolbox texture that a renderer draws into.
    %
    %   r = filly.Renderer.forWindow(win);
    %   tgt = filly.PTBTarget(r, win);              % covers the window
    %   tgt = filly.PTBTarget(r, win, width, height);
    %   r.render(scene, tgt);
    %   tgt.draw();                                 % or tgt.draw(destinationRect, ...)
    %   Screen('Flip', win);
    %
    %   draw() takes the arguments of Screen('DrawTexture') that follow sourceRect:
    %   destinationRect, rotationAngle, filterMode, globalAlpha, modulateColor, and so on. The
    %   default destination is the texture's size, centered. Filament writes premultiplied
    %   color; for blending, use Screen('BlendFunction', win, GL_ONE, GL_ONE_MINUS_SRC_ALPHA).
    %
    %   For several draws of one frame, call acquire() once before them and release() after.
    %   Make all filly calls outside Screen('BeginOpenGL'): there, a userspace context can be
    %   current, and filly raises a 'filly:interop' error.
    %
    %   PTB owns the texture: Screen('Close', tgt.texture) deletes it. close(tgt) or the
    %   renderer's close releases it after Filament no longer uses it. After `sca`, both still
    %   release filly's side and make no OpenGL calls.

    properties (SetAccess = private)
        window = []
        % The PTB texture handle for Screen('DrawTexture').
        texture = []
        % The OpenGL texture name.
        glTexture = 0
    end
    properties (Access = private)
        SourceRect
        DestinationRect
    end

    methods
        function obj = PTBTarget(renderer, window, width, height)
            if nargin < 3
                [width, height] = Screen('WindowSize', window);
            end
            [id, glTexture] = filly.internal.ptbCreate(renderer, window, width, height, ...
                @(gl) filly_mex('Renderer.importGlTarget', renderer.Id, gl, width, height));
            obj@filly.ImportedTarget(renderer, id);
            obj.window = window;
            obj.glTexture = glTexture;
            try
                % Flag 32: Screen('Close') without a handle does not delete the texture.
                obj.texture = Screen('SetOpenGLTexture', window, [], glTexture, 3553, width, height, 32, [], 32);
            catch err
                filly_mex('ImportedTarget.close', id);
                filly_mex('deleteHostTexture', glTexture);
                obj.window = [];
                rethrow(err);
            end
            renderer.HostTextures(end + 1, :) = [window, obj.texture, glTexture];
            % PTB insets the source of GL_TEXTURE_2D textures by half a texel at the right and
            % bottom, which blurs a 1:1 draw with bilinear filtering (phase 0, matlab/PLAN.md).
            % This source rectangle cancels the inset, so texture coordinates span [0, 1].
            obj.SourceRect = [0, -0.5, width + 0.5, height];
            obj.DestinationRect = CenterRect([0 0 width height], Screen('Rect', window));
        end

        function delete(obj)
            if ~isempty(obj.window)
                try
                    close(obj);
                catch
                end
            end
        end

        function draw(obj, destinationRect, varargin)
            % Draw with Screen('DrawTexture') between acquire and release.
            if nargin < 2 || isempty(destinationRect)
                destinationRect = obj.DestinationRect;
            end
            filly.internal.ptbActivate(obj.window);
            filly_mex('ImportedTarget.acquire', obj.Id);
            try
                Screen('DrawTexture', obj.window, obj.texture, obj.SourceRect, destinationRect, varargin{:});
            catch err
                filly_mex('ImportedTarget.release', obj.Id);
                rethrow(err);
            end
            filly_mex('ImportedTarget.release', obj.Id);
        end

        function acquire(obj)
            filly.internal.ptbActivate(obj.window);
            filly_mex('ImportedTarget.acquire', obj.Id);
        end

        function release(obj)
            filly.internal.ptbActivate(obj.window);
            filly_mex('ImportedTarget.release', obj.Id);
        end

        function close(obj)
            % Release Filament's side, then the PTB texture. Repeated calls do nothing.
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
            open = filly.internal.ptbHostOpen(window, obj.texture, obj.glTexture);
            if open
                filly.internal.ptbActivate(window);
            end
            try
                filly_mex('ImportedTarget.close', obj.Id);
            catch err
                filly.PTBTarget.forget(obj, row, window);
                rethrow(err);
            end
            filly.PTBTarget.forget(obj, row, window);
        end
    end

    methods (Static, Access = private)
        function forget(obj, row, window)
            % The renderer's list holds the texture until here; a closed renderer already
            % released it.
            if isempty(row)
                return
            end
            obj.Owner.HostTextures(row, :) = [];
            filly.internal.ptbRelease([window, obj.texture, obj.glTexture]);
        end
    end
end
