classdef Node < filly.Object
    % NODE  A node of a model: its transform, morph weights, and materials.
    %   index is the glTF node index (0-based, as in the file); the model root has -1.

    properties (Dependent)
        transform
        position
        scale
        quaternion
        rotationEulerDeg
    end
    properties (Dependent, SetAccess = private)
        name
        meshName
        index
        parent
        children
        bounds
        morphTargetCount
    end

    methods
        function obj = Node(owner, id)
            obj@filly.Object(owner, id);
        end

        function setMorphWeights(nodes, weights)
            % One weight per morph target. For an array of N nodes, weights is N x targets
            % and one call sets them all; inputs are checked first, so an error changes
            % nothing.
            filly_mex('Node.setMorphWeights', filly.Object.ids(nodes), weights);
        end

        function setTransforms(nodes, transforms)
            % Set the transforms of an array of N nodes from a 4 x 4 x N array, in one call.
            % The nodes may come from any models of one renderer. Inputs are checked first,
            % so an error changes nothing.
            filly_mex('Node.setTransforms', filly.Object.ids(nodes), transforms);
        end

        function m = material(obj, slot)
            % This node's own material in a primitive slot (1-based, default 1). The first
            % call copies the shared glTF material.
            if nargin < 2, slot = 1; end
            m = filly.Material(obj.Owner, filly_mex('Node.material', obj.Id, slot));
        end

        function v = get.name(obj), v = filly_mex('get', obj.Id, 'name'); end
        function v = get.meshName(obj), v = filly_mex('get', obj.Id, 'meshName'); end
        function v = get.index(obj), v = filly_mex('get', obj.Id, 'index'); end
        function v = get.parent(obj)
            id = filly_mex('get', obj.Id, 'parent');
            if isempty(id), v = []; else, v = filly.Node(obj.Owner, id); end
        end
        function v = get.children(obj), v = filly.Object.wrap(@filly.Node, obj.Owner, filly_mex('get', obj.Id, 'children')); end
        function v = get.bounds(obj), v = filly_mex('get', obj.Id, 'bounds'); end
        function v = get.morphTargetCount(obj), v = filly_mex('get', obj.Id, 'morphTargetCount'); end
        function v = get.transform(obj), v = filly_mex('get', obj.Id, 'transform'); end
        function set.transform(obj, v), filly_mex('set', obj.Id, 'transform', v); end
        function v = get.position(obj), v = filly_mex('get', obj.Id, 'position'); end
        function set.position(obj, v), filly_mex('set', obj.Id, 'position', v); end
        function v = get.scale(obj), v = filly_mex('get', obj.Id, 'scale'); end
        function set.scale(obj, v), filly_mex('set', obj.Id, 'scale', v); end
        function v = get.quaternion(obj), v = filly_mex('get', obj.Id, 'quaternion'); end
        function set.quaternion(obj, v), filly_mex('set', obj.Id, 'quaternion', v); end
        function v = get.rotationEulerDeg(obj), v = filly_mex('get', obj.Id, 'rotationEulerDeg'); end
        function set.rotationEulerDeg(obj, v), filly_mex('set', obj.Id, 'rotationEulerDeg', v); end
    end
end
