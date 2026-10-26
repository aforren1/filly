classdef ImportedTexture < filly.Object
    % IMPORTEDTEXTURE  A host OpenGL texture that materials sample.
    %   The host writes the texture between beginWrite() and endWrite(), with the host
    %   context current. For Psychtoolbox, filly.PTBTexture creates and wraps one.

    properties (Dependent, SetAccess = private)
        width
        height
        colorSpace
        writing
        closed
    end

    methods
        function obj = ImportedTexture(owner, id)
            obj@filly.Object(owner, id);
        end

        function beginWrite(obj)
            % Queue a GPU wait for frames that still sample the texture.
            filly_mex('ImportedTexture.beginWrite', obj.Id);
        end

        function endWrite(obj)
            % Fence the host's writes; the next render that samples the texture waits for it.
            filly_mex('ImportedTexture.endWrite', obj.Id);
        end

        function close(obj)
            % Remove the texture from all materials. The host texture is not deleted.
            filly_mex('ImportedTexture.close', obj.Id);
        end

        function v = get.width(obj), v = filly_mex('get', obj.Id, 'width'); end
        function v = get.height(obj), v = filly_mex('get', obj.Id, 'height'); end
        function v = get.colorSpace(obj), v = filly_mex('get', obj.Id, 'colorSpace'); end
        function v = get.writing(obj), v = filly_mex('get', obj.Id, 'writing'); end
        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
    end
end
