classdef Preparation < filly.Object
    % PREPARATION  Shader compilation that renderer.prepare() started.

    properties (Dependent, SetAccess = private)
        pending
    end

    methods
        function obj = Preparation(owner, id)
            obj@filly.Object(owner, id);
        end

        function tf = ready(obj)
            % Let the backend make progress, then report whether every program is compiled.
            tf = filly_mex('Preparation.ready', obj.Id);
        end

        function v = get.pending(obj), v = filly_mex('get', obj.Id, 'pending'); end
    end
end
