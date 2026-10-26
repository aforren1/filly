classdef Light < filly.Object
    % LIGHT  A punctual light that a scene created or a model imported.
    %   Properties that the light type lacks read as [] and raise an error when set.

    properties (Dependent)
        color
        intensity
        position
        direction
        range
        coneDeg
        castsShadows
        shadowMapSize
        shadowConstantBias
        shadowNormalBias
    end
    properties (Dependent, SetAccess = private)
        type
        node
        closed
    end

    methods
        function obj = Light(owner, id)
            obj@filly.Object(owner, id);
        end

        function close(obj)
            filly_mex('Light.close', obj.Id);
        end

        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
        function v = get.type(obj), v = filly_mex('get', obj.Id, 'type'); end
        function v = get.node(obj)
            id = filly_mex('get', obj.Id, 'node');
            if isempty(id), v = []; else, v = filly.Node(obj.Owner, id); end
        end
        function v = get.color(obj), v = filly_mex('get', obj.Id, 'color'); end
        function set.color(obj, v), filly_mex('set', obj.Id, 'color', v); end
        function v = get.intensity(obj), v = filly_mex('get', obj.Id, 'intensity'); end
        function set.intensity(obj, v), filly_mex('set', obj.Id, 'intensity', v); end
        function v = get.position(obj), v = filly_mex('get', obj.Id, 'position'); end
        function set.position(obj, v), filly_mex('set', obj.Id, 'position', v); end
        function v = get.direction(obj), v = filly_mex('get', obj.Id, 'direction'); end
        function set.direction(obj, v), filly_mex('set', obj.Id, 'direction', v); end
        function v = get.range(obj), v = filly_mex('get', obj.Id, 'range'); end
        function set.range(obj, v), filly_mex('set', obj.Id, 'range', v); end
        function v = get.coneDeg(obj), v = filly_mex('get', obj.Id, 'coneDeg'); end
        function set.coneDeg(obj, v), filly_mex('set', obj.Id, 'coneDeg', v); end
        function v = get.castsShadows(obj), v = filly_mex('get', obj.Id, 'castsShadows'); end
        function set.castsShadows(obj, v), filly_mex('set', obj.Id, 'castsShadows', v); end
        function v = get.shadowMapSize(obj), v = filly_mex('get', obj.Id, 'shadowMapSize'); end
        function set.shadowMapSize(obj, v), filly_mex('set', obj.Id, 'shadowMapSize', v); end
        function v = get.shadowConstantBias(obj), v = filly_mex('get', obj.Id, 'shadowConstantBias'); end
        function set.shadowConstantBias(obj, v), filly_mex('set', obj.Id, 'shadowConstantBias', v); end
        function v = get.shadowNormalBias(obj), v = filly_mex('get', obj.Id, 'shadowNormalBias'); end
        function set.shadowNormalBias(obj, v), filly_mex('set', obj.Id, 'shadowNormalBias', v); end
    end
end
