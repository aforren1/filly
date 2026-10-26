function shape = box(varargin)
% BOX  An axis-aligned box centered on the origin. Each face has its own vertices and full UVs.
%   Options: 'Width' (1), 'Height' (1), 'Depth' (1). Fields as for filly.shapes.plane.
o = filly.internal.options(struct('Width', 1, 'Height', 1, 'Depth', 1), varargin);
shape = filly_mex('shape', 'box', o.Width, o.Height, o.Depth);
end
