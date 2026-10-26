classdef ImportedTarget < filly.Object
    % IMPORTEDTARGET  A host OpenGL texture that the renderer draws into.
    %   The host samples it between acquire() and release(), with the host context current.
    %   Nested acquisitions only count depth. For Psychtoolbox, use filly.PTBTarget.

    properties (Dependent, SetAccess = private)
        width
        height
        acquired
        closed
    end

    methods
        function obj = ImportedTarget(owner, id)
            obj@filly.Object(owner, id);
        end

        function acquire(obj)
            % Queue a GPU wait for the last render before host draws that sample the texture.
            filly_mex('ImportedTarget.acquire', obj.Id);
        end

        function release(obj)
            % Fence the host's draws; the next render waits for them before it overwrites.
            filly_mex('ImportedTarget.release', obj.Id);
        end

        function close(obj)
            filly_mex('ImportedTarget.close', obj.Id);
        end

        function v = get.width(obj), v = filly_mex('get', obj.Id, 'width'); end
        function v = get.height(obj), v = filly_mex('get', obj.Id, 'height'); end
        function v = get.acquired(obj), v = filly_mex('get', obj.Id, 'acquired'); end
        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
    end
end
