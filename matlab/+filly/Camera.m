classdef Camera < filly.Object
    % CAMERA  A camera of the renderer, which any of its scenes can use.
    %   A new camera has a perspective projection: 30 degrees vertical, near 0.1, far 100, and
    %   the aspect of the render target. Matrices are 4 x 4 with column vectors.

    properties (Dependent)
        exposure
        focusDistance
        aperture
        position
        transform
    end
    properties (Dependent, SetAccess = private)
        viewMatrix
        projection
        node
        closed
    end

    methods
        function obj = Camera(owner, id)
            obj@filly.Object(owner, id);
        end

        function close(obj)
            filly_mex('Camera.close', obj.Id);
        end

        function setPerspective(obj, varargin)
            % Options: 'FovYDeg' (required), 'Aspect' ([] follows the target), 'Near', 'Far'.
            o = filly.internal.options(struct('FovYDeg', [], 'Aspect', [], 'Near', [], 'Far', []), varargin);
            if isempty(o.FovYDeg), error('filly:invalidArgument', 'setPerspective needs ''FovYDeg'''); end
            filly_mex('Camera.setPerspective', obj.Id, o.FovYDeg, o.Aspect, o.Near, o.Far);
        end

        function setLensProjection(obj, varargin)
            % Options: 'FocalLengthMm' (required, 24 mm sensor height), 'Aspect', 'Near', 'Far'.
            o = filly.internal.options(struct('FocalLengthMm', [], 'Aspect', [], 'Near', [], 'Far', []), varargin);
            if isempty(o.FocalLengthMm), error('filly:invalidArgument', 'setLensProjection needs ''FocalLengthMm'''); end
            filly_mex('Camera.setLensProjection', obj.Id, o.FocalLengthMm, o.Aspect, o.Near, o.Far);
        end

        function setOrthographic(obj, varargin)
            % Either 'Left', 'Right', 'Bottom', 'Top', or 'Height' and optionally 'Center'
            % ([0 0]), whose width follows the target aspect. Options 'Near' and 'Far'.
            o = filly.internal.options(struct('Left', [], 'Right', [], 'Bottom', [], 'Top', [], 'Height', [], ...
                'Center', [], 'Near', [], 'Far', []), varargin);
            bounds = ~isempty(o.Left) || ~isempty(o.Right) || ~isempty(o.Bottom) || ~isempty(o.Top);
            if ~isempty(o.Height)
                if bounds, error('filly:invalidArgument', 'Give either Left/Right/Bottom/Top or Height, not both'); end
                filly_mex('Camera.setOrthographicHeight', obj.Id, o.Height, o.Center, o.Near, o.Far);
                return
            end
            if ~isempty(o.Center), error('filly:invalidArgument', 'Center requires Height'); end
            if isempty(o.Left) || isempty(o.Right) || isempty(o.Bottom) || isempty(o.Top)
                error('filly:invalidArgument', 'Give all of Left, Right, Bottom, and Top, or give Height');
            end
            filly_mex('Camera.setOrthographic', obj.Id, o.Left, o.Right, o.Bottom, o.Top, o.Near, o.Far);
        end

        function lookAt(obj, target, up)
            % Aim at a point. up defaults to [0 1 0].
            if nargin < 3, up = [0 1 0]; end
            filly_mex('Camera.lookAt', obj.Id, target, up);
        end

        function distance = frame(obj, target, varargin)
            % Aim at a Model, a Node, or a 2 x 3 box [min; max] and move so that it fills
            % 'Fill' (0.8) of the view. Options: 'Direction', 'Up' ([0 1 0]), 'Fit'
            % ('sphere' or 'box'), 'Near', 'Far', 'Aspect'. Returns the distance to the center.
            o = filly.internal.options(struct('Fill', [], 'Direction', [], 'Up', [], 'Fit', '', 'Near', [], ...
                'Far', [], 'Aspect', []), varargin);
            if isa(target, 'filly.Object'), target = target.Id; end
            distance = filly_mex('Camera.frame', obj.Id, target, o.Fill, o.Direction, o.Up, ...
                filly.internal.text(o.Fit), o.Near, o.Far, o.Aspect);
        end

        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
        function v = get.exposure(obj), v = filly_mex('get', obj.Id, 'exposure'); end
        function set.exposure(obj, v), filly_mex('set', obj.Id, 'exposure', v); end
        function v = get.focusDistance(obj), v = filly_mex('get', obj.Id, 'focusDistance'); end
        function set.focusDistance(obj, v), filly_mex('set', obj.Id, 'focusDistance', v); end
        function v = get.aperture(obj), v = filly_mex('get', obj.Id, 'aperture'); end
        function set.aperture(obj, v), filly_mex('set', obj.Id, 'aperture', v); end
        function v = get.position(obj), v = filly_mex('get', obj.Id, 'position'); end
        function set.position(obj, v), filly_mex('set', obj.Id, 'position', v); end
        function v = get.transform(obj), v = filly_mex('get', obj.Id, 'transform'); end
        function set.transform(obj, v), filly_mex('set', obj.Id, 'transform', v); end
        function v = get.viewMatrix(obj), v = filly_mex('get', obj.Id, 'viewMatrix'); end
        function v = get.projection(obj), v = filly_mex('get', obj.Id, 'projection'); end
        function v = get.node(obj)
            id = filly_mex('get', obj.Id, 'node');
            if isempty(id), v = []; else, v = filly.Node(obj.Owner, id); end
        end
    end
end
