classdef Environment < filly.Object
    % ENVIRONMENT  Image-based lighting that any scene of the renderer can use.

    properties (Dependent, SetAccess = private)
        closed
    end

    methods
        function obj = Environment(owner, id)
            obj@filly.Object(owner, id);
        end

        function close(obj)
            % Remove the environment from the scenes that use it and release it.
            filly_mex('Environment.close', obj.Id);
        end

        function v = get.closed(obj), v = filly_mex('get', obj.Id, 'closed'); end
    end
end
