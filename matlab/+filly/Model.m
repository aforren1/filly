classdef Model < filly.Object
    % MODEL  One instance of a glTF asset, or a mesh from arrays, in a scene.
    %   Node, light, and camera keys are a name or a glTF node index (0-based, as in the file).
    %   Animation and variant keys are a name or a 1-based position in animations or variants.

    properties (Dependent)
        transform
        position
        visible
        scale
        quaternion
        rotationEulerDeg
    end
    properties (Dependent, SetAccess = private)
        closed
        memory
        cameras
        lights
        animations
        variants
        bounds
        root
        nodes
        nodeNames
        materialNames
        isMesh
        vertexCount
    end

    methods
        function obj = Model(owner, id)
            obj@filly.Object(owner, id);
        end

        function close(obj)
            filly_mex('Model.close', obj.Id);
        end

        function n = node(obj, key)
            n = filly.Node(obj.Owner, filly_mex('Model.node', obj.Id, filly.Model.key(key)));
        end

        function c = camera(obj, key)
            c = filly.Camera(obj.Owner, filly_mex('Model.camera', obj.Id, filly.Model.key(key)));
        end

        function l = light(obj, key)
            l = filly.Light(obj.Owner, filly_mex('Model.light', obj.Id, filly.Model.key(key)));
        end

        function m = material(obj, name)
            % The glTF material that every mesh slot using it shares in this model.
            m = filly.Material(obj.Owner, filly_mex('Model.material', obj.Id, filly.internal.text(name)));
        end

        function applyAnimation(obj, key, time, varargin)
            % Pose the model at time (seconds) of a clip. Option 'Loop' (true).
            if isempty(varargin)
                filly_mex('Model.applyAnimation', obj.Id, filly.Model.key(key), time);
                return
            end
            o = filly.internal.options(struct('Loop', true), varargin);
            filly_mex('Model.applyAnimation', obj.Id, filly.Model.key(key), time, o.Loop);
        end

        function resetAnimation(obj)
            filly_mex('Model.resetAnimation', obj.Id);
        end

        function applyVariant(obj, key)
            filly_mex('Model.applyVariant', obj.Id, filly.Model.key(key));
        end

        function updateMesh(obj, varargin)
            % Replace vertex arrays of a generated mesh. Options: 'Positions', 'Normals', 'Uvs',
            % 'Colors', each with one row per vertex. Every model of the same asset from
            % renderer.createMeshAsset() shows the change.
            o = filly.internal.options(struct('Positions', [], 'Normals', [], 'Uvs', [], 'Colors', []), varargin);
            filly_mex('Model.updateMesh', obj.Id, o.Positions, o.Normals, o.Uvs, o.Colors);
        end

        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
        function v = get.memory(obj), v = filly_mex('get', obj.Id, 'memory'); end
        function v = get.cameras(obj), v = filly.Object.wrap(@filly.Camera, obj.Owner, filly_mex('get', obj.Id, 'cameras')); end
        function v = get.lights(obj), v = filly.Object.wrap(@filly.Light, obj.Owner, filly_mex('get', obj.Id, 'lights')); end
        function v = get.animations(obj), v = filly_mex('get', obj.Id, 'animations'); end
        function v = get.variants(obj), v = filly_mex('get', obj.Id, 'variants'); end
        function v = get.bounds(obj), v = filly_mex('get', obj.Id, 'bounds'); end
        function v = get.root(obj), v = filly.Node(obj.Owner, filly_mex('get', obj.Id, 'root')); end
        function v = get.nodes(obj), v = filly.Object.wrap(@filly.Node, obj.Owner, filly_mex('get', obj.Id, 'nodes')); end
        function v = get.nodeNames(obj), v = filly_mex('get', obj.Id, 'nodeNames'); end
        function v = get.materialNames(obj), v = filly_mex('get', obj.Id, 'materialNames'); end
        function v = get.isMesh(obj), v = filly_mex('get', obj.Id, 'isMesh'); end
        function v = get.vertexCount(obj), v = filly_mex('get', obj.Id, 'vertexCount'); end
        function v = get.transform(obj), v = filly_mex('get', obj.Id, 'transform'); end
        function set.transform(obj, v), filly_mex('set', obj.Id, 'transform', v); end
        function v = get.position(obj), v = filly_mex('get', obj.Id, 'position'); end
        function set.position(obj, v), filly_mex('set', obj.Id, 'position', v); end
        function v = get.visible(obj), v = filly_mex('get', obj.Id, 'visible'); end
        function set.visible(obj, v), filly_mex('set', obj.Id, 'visible', v); end
        function v = get.scale(obj), v = filly_mex('get', obj.Id, 'scale'); end
        function set.scale(obj, v), filly_mex('set', obj.Id, 'scale', v); end
        function v = get.quaternion(obj), v = filly_mex('get', obj.Id, 'quaternion'); end
        function set.quaternion(obj, v), filly_mex('set', obj.Id, 'quaternion', v); end
        function v = get.rotationEulerDeg(obj), v = filly_mex('get', obj.Id, 'rotationEulerDeg'); end
        function set.rotationEulerDeg(obj, v), filly_mex('set', obj.Id, 'rotationEulerDeg', v); end
    end

    methods (Static, Hidden)
        function key = key(key)
            if ~isnumeric(key), key = filly.internal.text(key); end
        end
    end
end
