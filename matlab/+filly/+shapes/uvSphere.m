function shape = uvSphere(varargin)
% UVSPHERE  A sphere centered on the origin with poles on the Y axis.
%   Options: 'Radius' (0.5), 'Segments' (32), 'Rings' (16). Fields as for filly.shapes.plane.
o = filly.internal.options(struct('Radius', 0.5, 'Segments', 32, 'Rings', 16), varargin);
shape = filly_mex('shape', 'uvSphere', o.Radius, o.Segments, o.Rings);
end
