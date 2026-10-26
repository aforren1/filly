function shape = cylinder(varargin)
% CYLINDER  A cylinder centered on the origin with its axis on Y.
%   Options: 'Radius' (0.5), 'Height' (1), 'Segments' (32), 'Caps' (true). Fields as for
%   filly.shapes.plane.
o = filly.internal.options(struct('Radius', 0.5, 'Height', 1, 'Segments', 32, 'Caps', true), varargin);
shape = filly_mex('shape', 'cylinder', o.Radius, o.Height, o.Segments, o.Caps);
end
