classdef PickQuery < filly.Object
    % PICKQUERY  A pick from renderer.pick(), answered by a later render of its scene.

    properties (Dependent, SetAccess = private)
        % [] for no model surface; otherwise a struct with model, node, position (world space),
        % and depth (scene units along the view direction). Raises an error until ready().
        result
        % stats.framesRendered just after the render that took the query; [] before it.
        frameNumber
    end

    methods
        function obj = PickQuery(owner, id)
            obj@filly.Object(owner, id);
        end

        function tf = ready(obj)
            % Deliver the answers that arrived, without waiting for the GPU.
            tf = filly_mex('PickQuery.ready', obj.Id);
        end

        function v = get.result(obj)
            v = filly_mex('get', obj.Id, 'result');
            if ~isempty(v)
                v.model = filly.Model(obj.Owner, v.model);
                v.node = filly.Node(obj.Owner, v.node);
            end
        end
        function v = get.frameNumber(obj), v = filly_mex('get', obj.Id, 'frameNumber'); end
    end
end
