classdef Texture < filly.Object
    % TEXTURE  A sampled texture with pixels from MATLAB, from renderer.createTexture().

    properties (Dependent, SetAccess = private)
        width
        height
        channels
        dtype
        colorSpace
        mipmaps
        closed
    end

    methods
        function obj = Texture(owner, id)
            obj@filly.Object(owner, id);
        end

        function update(obj, pixels)
            % Replace the pixels with an array of the texture's size, channels, and class.
            % The array is free again when update returns.
            filly_mex('Texture.update', obj.Id, pixels);
        end

        function close(obj)
            filly_mex('Texture.close', obj.Id);
        end

        function v = get.width(obj), v = filly_mex('get', obj.Id, 'width'); end
        function v = get.height(obj), v = filly_mex('get', obj.Id, 'height'); end
        function v = get.channels(obj), v = filly_mex('get', obj.Id, 'channels'); end
        function v = get.dtype(obj), v = filly_mex('get', obj.Id, 'dtype'); end
        function v = get.colorSpace(obj), v = filly_mex('get', obj.Id, 'colorSpace'); end
        function v = get.mipmaps(obj), v = filly_mex('get', obj.Id, 'mipmaps'); end
        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
    end
end
