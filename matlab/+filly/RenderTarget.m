classdef RenderTarget < filly.Object
    % RENDERTARGET  An offscreen color target whose pixels read() copies to MATLAB.

    properties (Dependent, SetAccess = private)
        width
        height
        closed
    end

    methods
        function obj = RenderTarget(owner, id)
            obj@filly.Object(owner, id);
        end

        function pixels = read(obj)
            % An H x W x 4 uint8 RGBA image, first row at the top. Waits for the GPU.
            pixels = filly_mex('RenderTarget.read', obj.Id);
        end

        function readback = beginRead(obj)
            % Queue a copy of the pixels and return a filly.Readback at once. Later renders
            % into the target do not change it; read() it after the next render.
            readback = filly.Readback(obj.Owner, filly_mex('RenderTarget.beginRead', obj.Id));
        end

        function close(obj)
            filly_mex('RenderTarget.close', obj.Id);
        end

        function v = get.width(obj), v = filly_mex('get', obj.Id, 'width'); end
        function v = get.height(obj), v = filly_mex('get', obj.Id, 'height'); end
        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
    end
end
