"""Write an ASCII STL cube (default 20 mm) — used by the CLI spike and the slow integration test.

Usage: python scripts/make_cube.py [out.stl] [size_mm]
"""
import sys


def cube_stl(size: float = 20.0) -> str:
    s = size
    v = [(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0), (0, 0, s), (s, 0, s), (s, s, s), (0, s, s)]
    # (normal, tri1, tri2) per face, CCW seen from outside
    faces = [
        ((0, 0, -1), (0, 2, 1), (0, 3, 2)),
        ((0, 0, 1), (4, 5, 6), (4, 6, 7)),
        ((0, -1, 0), (0, 1, 5), (0, 5, 4)),
        ((1, 0, 0), (1, 2, 6), (1, 6, 5)),
        ((0, 1, 0), (2, 3, 7), (2, 7, 6)),
        ((-1, 0, 0), (3, 0, 4), (3, 4, 7)),
    ]
    out = ["solid cube"]
    for n, *tris in faces:
        for t in tris:
            out.append(f"  facet normal {n[0]} {n[1]} {n[2]}")
            out.append("    outer loop")
            for i in t:
                out.append(f"      vertex {v[i][0]} {v[i][1]} {v[i][2]}")
            out.append("    endloop")
            out.append("  endfacet")
    out.append("endsolid cube")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "cube.stl"
    size = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    with open(path, "w") as f:
        f.write(cube_stl(size))
    print(path)
