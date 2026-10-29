classdef Readback < filly.Object
    % READBACK  A copy of a render target's pixels that target.beginRead() queued.
    %
    % Later renders into the target do not change it, so a loop can render the next frame
    % before it reads this one:
    %
    %   renderer.render(scene, target);
    %   pending = target.beginRead();
    %   ... render the next frame ...
    %   pixels = pending.read();

    properties (Dependent, SetAccess = private)
        % stats.framesRendered when beginRead() queued the copy.
        frameNumber
        % True after read() or close(), and when the target or the renderer closes.
        closed
    end

    methods
        function obj = Readback(owner, id)
            obj@filly.Object(owner, id);
        end

        function tf = ready(obj)
            % Report whether the GPU has finished the copy, without waiting for it.
            tf = filly_mex('Readback.ready', obj.Id);
        end

        function pixels = read(obj)
            % An H x W x 4 uint8 RGBA image, first row at the top. Waits for the copy, then
            % closes the readback.
            pixels = filly_mex('Readback.read', obj.Id);
        end

        function close(obj)
            % Release the copy without reading it.
            filly_mex('Readback.close', obj.Id);
        end

        function v = get.frameNumber(obj), v = filly_mex('get', obj.Id, 'frameNumber'); end
        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
    end
end
