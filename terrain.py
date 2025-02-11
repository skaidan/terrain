from logging import exception
import language

import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import scipy.spatial as spl
import scipy.sparse as spa
import scipy.sparse.csgraph as csg
import scipy.sparse.linalg as sla
from collections import defaultdict
import heapq
import noise
import pickle
import gzip
import itertools

tex = True
sizemap = 15

if tex:
    plt.rcParams["text.usetex"] = False
    plt.rcParams["font.family"] = "palatino"
else:
    plt.rcParams["font.family"] = "Palatino Linotype"

plt.rcParams["font.size"] = 10


def distance(a, b):
    disp2 = (a - b) ** 2
    if len(disp2.shape) == 2:
        return np.sum(disp2, 1) ** 0.5
    else:
        return np.sum(disp2) ** 0.5


def trislope(xys, zs):
    a = np.matrix(np.column_stack((xys, [1, 1, 1])))
    b = np.asarray(zs).reshape((3, 1))
    x = a.I * b
    return x[0, 0], x[1, 0]


def relaxpts(pts, idxs, n=1):
    adj = defaultdict(list)
    for p1, p2 in idxs:
        adj[p1].append(p2)
        adj[p2].append(p1)
    for _ in range(n):
        newpts = pts.copy()
        for p in adj:
            if len(adj[p]) == 1:
                continue
            adjs = adj[p] + [p]
            newpts[p, :] = np.mean(pts[adjs, :], 0)
        pts = newpts
    return [[(pts[p1, 0], pts[p1, 1]), (pts[p2, 0], pts[p2, 1])] for p1, p2 in idxs]


def mergelines(segs):
    n = len(segs)
    segs = set((tuple(a), tuple(b)) for (a, b) in segs)

    assert len(segs) == n

    adjs = defaultdict(list)
    for a, b in segs:
        adjs[a].append((a, b))
        adjs[b].append((a, b))
    lines = []
    line = None
    nremoved = 0
    length = 0
    while segs:
        if line is None:
            line = list(segs.pop())
            nremoved += 1
        found = None

        for seg in adjs[line[-1]]:
            if seg not in segs:
                continue

            if seg[0] == line[-1]:
                line.append(seg[1])
                found = seg
                break

            elif seg[1] == line[-1]:
                line.append(seg[0])
                found = seg
                break

        if found:
            segs.remove(found)
            nremoved += 1
            continue

        for seg in adjs[line[0]]:
            if seg not in segs:
                continue

            if seg[0] == line[0]:
                line.insert(0, seg[1])
                found = seg
                break

            elif seg[1] == line[0]:
                line.insert(0, seg[0])
                found = seg
                break

        if found:
            segs.remove(found)
            nremoved += 1
            continue
        # nothing found

        lines.append(mpl.path.Path(line))
        length += len(line) - 1
        line = None

    assert nremoved == n, f"Got {n}, Removed {nremoved}"

    if line is not None:
        length += len(line) - 1
        lines.append(mpl.path.Path(line))

    print(length)
    return lines


def load(filename):
    with gzip.open(filename) as f:
        return pickle.loads(f.read())


city_counts = {
    "shore": (12, 20),
    "island": (5, 10),
    "mountain": (8, 16),
    "desert": (5, 10),
}
terr_counts = {"shore": (3, 7), "island": (2, 4), "mountain": (30, 80), "desert": (3, 5)}
riverpercs = {"shore": 5, "island": 3, "mountain": 8, "desert": 1}
forests_counts = {"shore": (40, 80), "island": (45, 75), "mountain": (50, 80), "desert": (2, 5)}

class MapException(Exception):
    pass


class MapGrid:
    def __init__(self, mode="shore", n=2 ** (sizemap)):
        """
        Creates a new MapGrid object
        :param mode: the map making mode
        :param n: number of map grid points
        """
        self.mode = mode
        self.lang = language.get_language()
        self.build_grid(n)

        if "/" in mode:
            self.mixed_heightmap()
            mode = mode.split("/")[0]
        else:
            self.single_heightmap(mode)

        self.finalize()

        self.riverperc = riverpercs[mode] * np.mean(self.elevation > 0)
        self.place_cities(np.random.randint(*city_counts[mode]))
        self.grow_territory(np.random.randint(*terr_counts[mode]))
        self.place_forests(np.random.randint(*forests_counts[mode]))
        self.name_places()
        self.path_cache = {}
        self.fill_path_cache(self.big_cities)

    @property
    def big_cities(self):
        return [c for c in self.cities if self.territories[c] == c]

    def save(self, filename):
        """
        Save MapGrid data to file.
        :param filename: the filename to save to
        """
        with gzip.open(filename, "w") as f:
            f.write(pickle.dumps(self))

    def build_grid(self, n):
        """
        Build the map grid. Spreads points and runs a few iterations of improvement to make a nice initial grid.
        :param n: number of points in the grid
        """

        # TODO: other shapes of point spread - e.g. Rectangle, Circle, Oval etc.

        self.pts = np.random.random((n, 2))
        self.improve_pts()
        self.vor = spl.Voronoi(self.pts)
        self.regions = [self.vor.regions[i] for i in self.vor.point_region]
        self.vxs = self.vor.vertices
        self.nvxs = self.vxs.shape[0]
        self.build_adjs()
        self.improve_vxs()
        self.calc_edges()
        self.distort_vxs()
        self.elevation = np.zeros(self.nvxs + 1)
        self.erodability = np.ones(self.nvxs)

    def do_erosion(self, n, rate=0.01):
        """
        Perform erosion by water flow.
        :param n: number of iterations to perform
        :param rate: the erosion rate
        """

        for _ in range(n):
            self.calc_downhill()
            self.calc_flow()
            self.calc_slopes()
            self.erode(rate)
            self.infill()
            self.elevation[-1] = 0

    def raise_sealevel(self, perc=35):
        """
        Raise the sea level to a certain percentile of the heightmap, rescales heights after shift.
        :param perc: the elevation percentile to place sea level at
        """
        maxheight = self.elevation.max()
        self.elevation -= np.percentile(self.elevation, perc)
        self.elevation *= maxheight / self.elevation.max()
        self.elevation[-1] = 0

    def finalize(self):
        self.calc_downhill()
        self.infill()
        self.calc_downhill()
        self.calc_flow()
        self.calc_slopes()
        self.calc_elevation_pts()

    def rift(self):
        v = np.random.normal(0, 5, (1, 2))
        side = 20 * (distance(self.dvxs, 0.5) ** 2 - 1)
        value = np.random.normal(0, 0.3)
        self.elevation[:-1] += np.arctan(side) * value

    def improve_pts(self, n=2):
        print("Improving points")
        for _ in range(n):
            vor = spl.Voronoi(self.pts)
            newpts = []
            for idx in range(len(vor.points)):
                pt = vor.points[idx, :]
                region = vor.regions[vor.point_region[idx]]
                if -1 in region:
                    newpts.append(pt)
                else:
                    vxs = np.asarray([vor.vertices[i, :] for i in region])
                    vxs[vxs < 0] = 0
                    vxs[vxs > 1] = 1
                    newpt = np.mean(vxs, 0)
                    newpts.append(newpt)
            self.pts = np.asarray(newpts)

    def improve_vxs(self):
        print("Improving vertices")
        n = self.nvxs
        for v in range(n):
            self.vxs[v, :] = np.mean(self.pts[self.vx_regions[v]], 0)

    def build_adjs(self):
        print("Building adjacencies")
        self.adj_pts = defaultdict(list)
        self.adj_vxs = defaultdict(list)
        for p1, p2 in self.vor.ridge_points:
            self.adj_pts[p1].append(p2)
            self.adj_pts[p2].append(p1)
        for v1, v2 in self.vor.ridge_vertices:
            self.adj_vxs[v1].append(v2)
            self.adj_vxs[v2].append(v1)
        self.vx_regions = defaultdict(list)
        for p in range(self.pts.shape[0]):
            for v in self.regions[p]:
                if v != -1:
                    self.vx_regions[v].append(p)
        self.tris = defaultdict(list)
        for p in range(self.pts.shape[0]):
            for v in self.regions[p]:
                self.tris[v].append(p)
        self.adj_mat = np.zeros((self.nvxs, 3), np.int32) - 1
        for k, v in self.adj_vxs.items():
            if k != -1:
                self.adj_mat[k, :] = v

    def calc_edges(self):
        n = self.nvxs
        self.edge = np.zeros(n, bool)
        for u in range(n):
            adjs = self.adj_vxs[u]
            if -1 in adjs:  # or \
                #                     np.all(self.vxs[adjs,0] > self.vxs[u,0]) or \
                #                     np.all(self.vxs[adjs,0] < self.vxs[u,0]) or \
                #                     np.all(self.vxs[adjs,1] > self.vxs[u,1]) or \
                #                     np.all(self.vxs[adjs,1] < self.vxs[u,1]):
                self.edge[u] = True

    def perlin(self, base=None):
        if base is None:
            base = np.random.randint(1000)

        perlin_noise = np.array([noise.pnoise2(x, y, lacunarity=1.7, octaves=3, base=base) for x, y in self.vxs])
        return perlin_noise

    def distort_vxs(self):
        """Distort the vertices with Perlin noise."""
        self.dvxs = self.vxs.copy()
        self.dvxs[:, 0] += self.perlin()
        self.dvxs[:, 1] += self.perlin()

    def single_heightmap(self, mode):
        modefunc = getattr(self, mode + "_heightmap")
        modefunc()
        return self.elevation[:-1].copy()

    def mixed_heightmap(self):
        mode1, mode2 = self.mode.split("/")
        hm1 = self.single_heightmap(mode1)
        print("HM1:", mode1, hm1.max(), hm1.min(), hm1.mean())
        hm2 = self.single_heightmap(mode2)
        print("HM2:", mode2, hm2.max(), hm2.min(), hm2.mean())
        mix = 20 * (self.dvxs[:, 0] - self.dvxs[:, 1])
        mixing = 1 / (1 + np.exp(-mix))
        print("MIX:", mixing.max(), mixing.min(), mixing.mean())
        self.elevation[:-1] = mixing * hm2 + (1 - mixing) * hm1
        self.clean_coast()

    def shore_heightmap(self):
        print("Calculating elevations")
        n = self.nvxs
        self.elevation = np.zeros(n + 1)
        self.elevation[:-1] = 0.5 + ((self.dvxs - 0.5) * np.random.normal(0, 4, (1, 2))).sum(1)
        self.elevation[:-1] += -4 * (np.random.random() - 0.5) * distance(self.vxs, 0.5)
        mountains = np.random.random((50, 2))
        for m in mountains:
            self.elevation[:-1] += np.exp(-distance(self.vxs, m) ** 2 / (2 * 0.05 ** 2)) ** 2
        print("Edge height:", self.elevation[:-1][self.edge].max())

        along = (((self.dvxs - 0.5) * np.random.normal(0, 2, (1, 2))).sum(1) + np.random.normal(0, 0.5)) * 10
        self.erodability = np.exp(4 * np.arctan(along))

        for i in range(5):
            self.rift()
            self.relax()
        for i in range(5):
            self.relax()
        self.normalize_elevation()

        sealevel = np.random.randint(20, 40)
        self.raise_sealevel(sealevel)
        self.do_erosion(100, 0.025)

        self.raise_sealevel(np.random.randint(sealevel, sealevel + 20))
        self.clean_coast()

    def island_heightmap(self):
        self.erodability[:] = np.exp(np.random.normal(0, 4))
        theta = np.random.random() * 2 * np.pi
        dvxs = 0.5 * (self.vxs + self.dvxs)
        x = (dvxs[:, 0] - 0.5) * np.cos(theta) + (dvxs[:, 1] - 0.5) * np.sin(theta)
        y = (dvxs[:, 0] - 0.5) * -np.sin(theta) + (dvxs[:, 1] - 0.5) * np.cos(theta)
        self.elevation[:-1] = 0.001 * (1 - distance(self.vxs, 0.5))
        manhattan = np.max(np.abs(self.vxs - 0.5), 1)
        xs = x[(manhattan < 0.3) & (np.abs(y) < 0.1)]
        n = np.random.randint(2, 6)
        for i, u in enumerate(np.linspace(xs.min(), xs.max(), n) - 0.05):
            v = np.random.normal(0, 0.05)
            d = ((x - u) ** 2 + (y - v) ** 2) ** 0.5
            eruption = np.maximum(1 + 0.2 * i - d / 0.15, 0) ** 2
            print("Erupting", self.vxs[np.argmax(eruption), :])
            self.elevation[:-1] = np.maximum(self.elevation[:-1], eruption)
            self.do_erosion(20, 0.005)
            self.raise_sealevel(80)

        self.do_erosion(40, 0.005)

        maxheight = 30 * (0.46 - manhattan)
        self.elevation[:-1] = np.minimum(maxheight, self.elevation[:-1])

        sealevel = np.random.randint(85, 92)
        self.raise_sealevel(sealevel)
        self.clean_coast()

    def mountain_heightmap(self):
        theta = np.random.random() * 2 * np.pi
        dvxs = 0.5 * (self.vxs + self.dvxs)
        x = (dvxs[:, 0] - 0.5) * np.cos(theta) + (dvxs[:, 1] - 0.5) * np.sin(theta)
        y = (dvxs[:, 0] - 0.5) * -np.sin(theta) + (dvxs[:, 1] - 0.5) * np.cos(theta)
        self.elevation[:-1] = 50 - 10 * np.abs(x)
        mountains = np.random.random((50, 2))
        for m in mountains:
            self.elevation[:-1] += np.exp(-distance(self.vxs, m) ** 2 / (2 * 0.05 ** 2)) ** 2
        self.erodability[:] = np.exp(50 - 10 * self.elevation[:-1])
        for _ in range(5):
            self.rift()
        self.do_erosion(250, 0.02)
        self.elevation *= 0.5
        self.elevation[:-1] -= self.elevation[:-1].min() - 0.5

    def desert_heightmap(self):
        theta = np.random.random() * 2 * np.pi
        dvxs = self.dvxs
        x = (dvxs[:, 0] - 0.5) * np.cos(theta) + (dvxs[:, 1] - 0.5) * np.sin(theta)
        y = (dvxs[:, 0] - 0.5) * -np.sin(theta) + (dvxs[:, 1] - 0.5) * np.cos(theta)
        self.elevation[:-1] = x + 20 + 2 * self.perlin()
        self.erodability[:] = 1
        self.do_erosion(50, 0.005)
        self.elevation[:-1] -= self.elevation[:-1].min() - 0.1

    def relax(self):
        newelev = np.zeros_like(self.elevation[:-1])
        for u in range(self.nvxs):
            adjs = [v for v in self.adj_vxs[u] if v != -1]
            if len(adjs) < 2:
                continue
            newelev[u] = np.mean(self.elevation[adjs])
        self.elevation[:-1] = newelev

    def normalize_elevation(self):
        self.elevation -= self.elevation.min()
        if self.elevation.max() > 0:
            self.elevation /= self.elevation.max()
        self.elevation[self.elevation < 0] = 0
        self.elevation = self.elevation ** 0.5

    def calc_elevation_pts(self):
        npts = self.pts.shape[0]
        self.elevation_pts = np.zeros(npts)
        for p in range(npts):
            if self.regions[p]:
                self.elevation_pts[p] = np.mean(self.elevation[self.regions[p]])

    def calc_downhill(self):
        n = self.nvxs
        dhidxs = np.argmin(self.elevation[self.adj_mat], 1)
        downhill = self.adj_mat[np.arange(n), dhidxs]
        downhill[self.elevation[:-1] <= self.elevation[downhill]] = -1
        downhill[self.edge] = -1
        self.downhill = downhill

    def calc_flow(self):
        n = self.nvxs
        rain = np.ones(n) / n
        i = self.downhill[self.downhill != -1]
        j = np.arange(n)[self.downhill != -1]
        dmat = spa.eye(n) - spa.coo_matrix((np.ones_like(i), (i, j)), (n, n)).tocsc()
        self.flow = sla.spsolve(dmat, rain)
        self.flow[self.elevation[:-1] <= 0] = 0

    def calc_slopes(self):
        dist = distance(self.vxs, self.vxs[self.downhill, :])
        self.slope = (self.elevation[:-1] - self.elevation[self.downhill]) / (dist + 1e-9)
        self.slope[self.downhill == -1] = 0

    def erode(self, max_step=0.05):
        """Perform erosion on the height map."""
        riverrate = -self.flow ** 0.5 * self.slope  # river erosion
        sloperate = -self.slope ** 2 * self.erodability  # slope smoothing
        rate = 1000 * riverrate + sloperate
        rate[self.elevation[:-1] <= 0] = 0
        self.elevation[:-1] += rate / np.abs(rate).max() * max_step

    def get_sinks(self):
        sinks = self.downhill.copy()
        water = self.elevation[:-1] <= 0
        sinklist = np.where((sinks == -1) & ~water & ~self.edge)[0]
        sinks[sinklist] = sinklist
        sinks[water] = -1
        while True:
            newsinks = sinks.copy()
            newsinks[~water] = sinks[sinks[~water]]
            newsinks[sinks == -1] = -1
            if np.all(sinks == newsinks):
                break
            sinks = newsinks
        return sinks

    def find_lowest_sill(self, sinks):
        h = 10000
        edges = np.where((sinks != -1) & np.any((sinks[self.adj_mat] == -1) & self.adj_mat != -1, 1))[0]

        for u in edges:
            adjs = [v for v in self.adj_vxs[u] if v != -1]
            for v in adjs:
                if sinks[v] == -1:
                    newh = max(self.elevation[v], self.elevation[u])
                    if newh < h:
                        h = newh
                        bestuv = u, v
        assert h < 10000
        u, v = bestuv
        return h, u, v

    def infill(self):
        tries = 0
        while True:
            tries += 1
            sinks = self.get_sinks()
            if np.all(sinks == -1):
                if tries > 1:
                    print(tries, "tries")
                return
            if tries == 1:
                print(
                    "Infilling",
                    np.sum(sinks != -1),
                    np.mean(self.vxs[sinks != -1, :], 0),
                )
            h, u, v = self.find_lowest_sill(sinks)
            sink = sinks[u]
            if self.downhill[v] != -1:
                self.elevation[v] = self.elevation[self.downhill[v]] + 1e-5
            sinkelev = self.elevation[:-1][sinks == sink]
            h = np.where(sinkelev < h, h + 0.001 * (h - sinkelev), sinkelev) + 1e-5
            self.elevation[:-1][sinks == sink] = h
            self.calc_downhill()

    def clean_coast(self, n=3, outwards=True):
        for _ in range(n):
            new_elev = self.elevation[:-1].copy()
            for u in range(self.nvxs):
                if self.edge[u] or self.elevation[u] <= 0:
                    continue
                adjs = self.adj_vxs[u]
                adjelevs = self.elevation[adjs]
                if np.sum(adjelevs > 0) == 1:
                    new_elev[u] = np.mean(adjelevs[adjelevs <= 0])
            self.elevation[:-1] = new_elev
            if outwards:
                for u in range(self.nvxs):
                    if self.edge[u] or self.elevation[u] > 0:
                        continue
                    adjs = self.adj_vxs[u]
                    adjelevs = self.elevation[adjs]
                    if np.sum(adjelevs <= 0) == 1:
                        new_elev[u] = np.mean(adjelevs[adjelevs > 0])
                self.elevation[:-1] = new_elev

    def place_cities(self, n=20):
        """
        Algorithm for placing cities, places cities close to large flows of water that are at or above sea level.

        :params n: Number of cities to place
        """
        self.city_score = self.flow ** 0.5
        self.city_score[self.elevation[:-1] <= 0] = -9999999
        self.cities = []
        while len(self.cities) < n:
            # location of potential new city is place with maximum score
            newcity = np.argmax(self.city_score)

            # Only place cities between 0.1 and 0.9 axes.
            city_max_ax = 0.95
            city_min_ax = 0.05
            # Chance that this location has no city, scales with number of cities placed so far
            if (
                np.random.random() < (len(self.cities) + 1) ** -0.2
                and city_min_ax < self.vxs[newcity, 0] < city_max_ax
                and city_min_ax < self.vxs[newcity, 1] < city_max_ax
            ):
                self.cities.append(newcity)

            # penalize city score for the newcity location.

            #import pdb
            #pdb.set_trace()
            self.city_score -= 0.01 * 1 / (distance(self.vxs, self.vxs[newcity, :]) + 1e-9)

    def edge_weight(self, u, v, territory=False):
        horiz = distance(self.vxs[u, :], self.vxs[v, :])
        vert = self.elevation[v] - self.elevation[u]
        if vert < 0:
            vert /= 10
        difficulty = 1 + (vert / horiz) ** 2
        if territory:
            difficulty += 100 * self.flow[u] ** 0.5
        else:
            if self.downhill[u] == v or self.downhill[v] == u:
                difficulty *= 0.9
        if self.elevation[u] <= 0:
            difficulty = 1
        if (self.elevation[u] <= 0) != (self.elevation[v] <= 0):
            difficulty = 2000 if territory else 200
        return horiz * difficulty

    def fill_path_cache(self, cities):
        cities = list(cities)
        n = self.vxs.shape[0]
        g = spa.dok_matrix((n + 2, n + 2))
        edge = self.extend_area(self.edge, 5)
        for u in range(n):
            for v in self.adj_vxs[u]:
                if not (edge[u] and edge[v]):
                    g[u, v] = self.edge_weight(u, v)
            if edge[u]:
                d = self.vxs[u, 0] - self.vxs[u, 1]
                if d < -0.5:
                    g[n, u] = 1e-12
                if d > 0.5:
                    g[u, n + 1] = 1e-12
        g = g.tocsc()
        tocities = cities + [n + 1]
        fromcities = cities + [n]
        dists, preds = csg.dijkstra(g, indices=fromcities, return_predecessors=True)

        for b in tocities:
            for i, a in enumerate(fromcities):
                if a == b:
                    continue
                p = [b]
                while p[0] != a:
                    p.insert(0, preds[i, p[0]])
                p = [x for x in p if x < n]
                d = dists[i, b]
                self.path_cache["topleft" if a == n else a, "bottomright" if b == n + 1 else b] = (p, d)

    def shortest_path(
        self,
        start,
        end,
    ):
        try:
            if self.path_cache[start, end]:
                return self.path_cache[start, end]
        except KeyError:
            print("WARNING: Uncached path search", start, end)
        except Exception as e:
            print(e.message)
        best_dir = {}
        q = []
        flipped = False

        length = 0
        if isinstance(end, str):
            flipped = True
            start, end = end, start
        heapq.heappush(q, (0, 0, end, -1))
        while start not in best_dir:
            _, dist, u, v = heapq.heappop(q)
            if u in best_dir:
                continue
            best_dir[u] = v

            if self.territories[u] < 2 ** (sizemap-3):
                if self.territories[u] == u:
                    path = [u]
                    while path[-1] != end:
                        path.append(best_dir[path[-1]])
                    if flipped:
                        self.path_cache[end, u] = path[::-1], dist
                        print("CACHE", len(self.path_cache))
                    else:
                        self.path_cache[u, end] = path, dist
                        print("CACHE", len(self.path_cache))
                length = dist
            if isinstance(start, str) and self.edge[u]:
                if (start == "topleft" and self.vxs[u, 0] - self.vxs[u, 1] < -0.5) or (
                    start == "bottomright" and self.vxs[u, 0] - self.vxs[u, 1] > 0.5
                ):
                    start = u
                    break
            for w in self.adj_vxs[u]:
                if w == -1 or w in best_dir or (self.edge[u] and self.edge[w]):
                    continue
                est = distance(self.vxs[end, :], self.vxs[w, :]) * 0.85
                d = dist + self.edge_weight(w, u)
                heapq.heappush(q, (d + est, d, w, u))        
        path = [start]
        while path[-1] != end:
            path.append(best_dir[path[-1]])
        if flipped:
            self.path_cache[end, start] = path[::-1], length
            print("CACHE", len(self.path_cache))
            return path[::-1], length
        else:
            self.path_cache[start, end] = path, length
            print("CACHE", len(self.path_cache))
            return path, length

    def grow_territory(self, n=7):
        done = np.zeros(self.nvxs, np.int32) - 1
        q = []
        for city in self.cities[:n]:
            heapq.heappush(q, (0, city, city))
        while q:
            dist, vx, city = heapq.heappop(q)
            if done[vx] != -1:
                continue
            done[vx] = city
            for u in self.adj_vxs[vx]:
                if done[u] != -1 or u == -1:
                    continue
                newdist = self.edge_weight(u, vx, territory=True)
                heapq.heappush(q, (dist + newdist, u, city))
        self.territories = done
    
    def place_forests(self, n=4):
        print("Forest")
        n = (2 ** sizemap) * (n) /100
        self.forest_score = self.flow ** 0.5
        self.forest_score[self.elevation[:-1] <= 0] = -9999999
        self.forests =set([])
        while len(self.forests) < n:
            # location of potential new forests is place with maximum score
            newforests = np.argmax(self.forest_score)
            if self.forest_score[newforests] == -9999999:
                break
            self.forest_score[newforests] = -9999999
            # Only place forest between 0 and 1 axes.
            forests_max_ax = 1
            forests_min_ax = 0
            # Chance that this location has no forest, scales with number of forests placed so far
            if (
                np.random.random() < (len(self.forests) + 1) ** -0.2
                and forests_min_ax < self.vxs[newforests, 0] < forests_max_ax
                and forests_min_ax < self.vxs[newforests, 1] < forests_max_ax
            ):
                flat_list = [item for sublist in [k for k in self.regions if newforests in k] for item in sublist]
                self.forests = self.forests.union(flat_list)
                #self.forests = set([item for sublist in self.forests for item in sublist])

    def extend_area(self, area, n):
        for _ in range(10):
            adj = self.adj_mat[area, :]
            area[adj[adj != -1]] = True
        return area

    def ordered_cities(self, cities):
        cities = cities
        dists = {}
        for c in cities:
            for d in cities:
                if c == d:
                    continue
                dists[c, d] = self.shortest_path(c, d)[1]
            dists["topleft", c] = self.shortest_path("topleft", c)[1]
            dists[c, "bottomright"] = self.shortest_path(c, "bottomright")[1]

        def totallength(seq):
            seq = ["topleft"] + list(seq) + ["bottomright"]
            return sum(dists[c, d] for c, d in zip(seq[:-1], seq[1:]))

        clist = min(itertools.permutations(cities), key=totallength)
        return clist
    
    def ordered_cities_region(self, cities):
        cities = cities
        dists = {}
        for c in cities:
            for d in cities:
                if c == d:
                    continue
                dists[c, d] = self.shortest_path(c, d)[1]

        def totallength(seq):
            return sum(dists[c, d] for c, d in zip(seq[:-1], seq[1:]))

        clist = min(itertools.permutations(cities), key=totallength)
        return clist

    RIVER_COLOR = mpl.cm.Blues(0.55)

    def plot(self, filename, rivers=True, cmap=mpl.cm.Greys, **kwargs):
        print("Plotting")
        fig = plt.figure(figsize=(6, 6))
        ax = fig.add_axes([0, 0, 1, 1])

        # Border patch
        bw = border_width = 0

        # Solid border
        vertices = [
            (0, 0),
            (0, 1),
            (1, 1),
            (1, 0),
            (0, 0),
            (bw, bw),
            (1 - bw, bw),
            (1 - bw, 1 - bw),
            (bw, 1 - bw),
            (bw, bw),
        ]
        codes = [
            mpl.path.Path.MOVETO,
            mpl.path.Path.LINETO,
            mpl.path.Path.LINETO,
            mpl.path.Path.LINETO,
            mpl.path.Path.LINETO,
            mpl.path.Path.MOVETO,
            mpl.path.Path.LINETO,
            mpl.path.Path.LINETO,
            mpl.path.Path.LINETO,
            mpl.path.Path.LINETO,
        ]

        # Diagonally split border
        # vertices = [
        #     (0, 0),
        #     (0, 1),
        #     (1, 1),
        #     (1, 0),
        #     (0, 0),
        #     (bw, bw),
        #     (1 - bw, bw),
        #     (1, 0),
        #     (1 - bw, bw),
        #     (1 - bw, 1 - bw),
        #     (1, 1),
        #     (1 - bw, 1 - bw),
        #     (bw, 1 - bw),
        #     (0, 1),
        #     (bw, 1 - bw),
        #     (bw, bw),
        #     (0, 0),
        # ]
        # codes = None

        path = mpl.path.Path(vertices, codes)
        patch = mpl.patches.PathPatch(path, fc="lightslategrey", ec="black", zorder=15)
        ax.add_patch(patch)

        elev = np.where(self.elevation > 0, 0.1, 0)
        good = ~self.extend_area(self.edge, 10)

        goodidxs = [i for i in range(self.nvxs) if good[i]]
        tris = [self.tris[i] for i in goodidxs]
        elevs = elev[goodidxs]
        elevations = self.elevation[goodidxs]

        # Plot slope lines
        slopelines = []
        slopes = []
        r = 0.25 * self.nvxs ** -0.5
        for i in goodidxs:
            # Do not put slope lines in ocean
            if self.elevation[i] <= 0:
                continue

            t = self.tris[i]
            s, s2 = trislope(self.pts[t, :], self.elevation_pts[t])
            s /= 10

            slopes.append(s)

            # Do not add a line if slope is below threshold
            if abs(s) < 0.1 + 0.3 * np.random.random():
                continue

            x, y = self.vxs[i, :]
            l = r * (1 + np.random.random()) * (1 - 0.2 * np.arctan(s) ** 2) * np.exp(s2 / 100)
            if abs(l * s) > 2 * r:
                n = int(abs(l * s / r))
                l /= n
                uv = np.random.normal(0, r / 2, (min(n, 4), 2))
                for u, v in uv:
                    slopelines.append([(x + u - l, y + v - l * s), (x + u + l, y + v + l * s)])
            else:
                slopelines.append([(x - l, y - l * s), (x + l, y + l * s)])

        slopecol = mpl.collections.LineCollection(slopelines)
        slopecol.set_zorder(1)
        slopecol.set_color("black")
        slopecol.set_linewidth(0.3)
        # ax.add_collection(slopecol)

        # Adjust slope values to fit cmap
        land_slopes = np.array(slopes)
        land_angles = np.sin(np.arctan(slopes))

        print(f"Slopes")
        print(f"Max: {max(land_angles)}")
        print(f"Min: {min(land_angles)}")
        print(f"Avg: {np.mean(land_angles)}")

        # Tone down gradient difference
        land_slopes *= 0.5
        land_angles *= 0.7

        # Slopes should be centered at 0.5 (flat ground = 0.5)
        land_slopes += 0.5
        land_angles += 0.5

        cmap = "copper"
        # cmap = "summer"

        # Plot land patches
        land = np.where(elevs > 0)[0]
        landpatches = [mpl.patches.Polygon(self.pts[tris[i], :], closed=True) for i in land]
        landpatchcol = mpl.collections.PatchCollection(landpatches, cmap=cmap, ec="face")

        land_heights = elevations[land]
        land_heights = land_heights - min(land_heights)
        land_heights *= 1 / max(land_heights)
        land_colors = land_heights * 0.30 + 0.35 + np.random.random(len(land_heights)) * 0.02 - 0.10
        # landpatchcol.set_array(land_colors)
        landpatchcol.set_array(land_angles)
        # landpatchcol.set_array(land_slopes)
        landpatchcol.set_clim([0.0, 1.0])
        landpatchcol.set_zorder(0)
        ax.add_collection(landpatchcol)

        # Plot sea patches
        sea = np.where(elevs <= 0)[0]
        if len(sea) > 0:
            seapatches = [mpl.patches.Polygon(self.pts[tris[i], :], closed=True) for i in sea]
            seapatchcol = mpl.collections.PatchCollection(seapatches, cmap="Blues", ec="face")

            # Random range of narrow range of cmap
            sea_heights = elevations[sea]
            sea_heights = abs(sea_heights) - min(sea_heights)
            sea_heights *= 1 / max(sea_heights)
            sea_colors = sea_heights * 0.80 + 0.10 + np.random.random(len(sea_heights)) * 0.02
            seapatchcol.set_array(sea_colors)
            seapatchcol.set_clim([0.0, 1.0])
            seapatchcol.set_zorder(10)
            ax.add_collection(seapatchcol)

        # Draw rivers
        if rivers:
            land = (
                good
                & (self.elevation[:-1] > 0)
                & (self.downhill != -1)
                & (self.flow > np.percentile(self.flow, 100 - self.riverperc))
            )
            rivers = relaxpts(self.vxs, [(u, self.downhill[u]) for u in range(self.nvxs) if land[u]])
            print(len(rivers), sum(land))
            rivers = mergelines(rivers)
            rivercol = mpl.collections.PathCollection(rivers, capstyle="round", joinstyle="round")
            rivercol.set_edgecolor(self.RIVER_COLOR)
            rivercol.set_linewidth(2)
            rivercol.set_facecolor("none")
            rivercol.set_zorder(25)
            rivercol.set_path_effects([pe.SimpleLineShadow((-0.5, 0.0)), pe.Normal()])
            ax.add_collection(rivercol)
        #Draw forests
        print("Draw forests")
        print(self.forests)
        for forest in self.forests:
            if forest < 2 ** sizemap:
                    d = ax.scatter(
                        self.vxs[forest, 0],
                        self.vxs[forest, 1],
                        s=5,
                        alpha=0.5,
                        c="green",
                        zorder=13,
                        linewidth=1.5,
                    )         

        # Draw cities
        print("Draw cities")
        bigcities = self.big_cities
        smallcities = [c for c in self.cities if c not in bigcities]
        c = ax.scatter(
            self.vxs[bigcities, 0],
            self.vxs[bigcities, 1],
            c="white",
            s=70,
            zorder=15,
            edgecolor="black",
            linewidth=1.5,
        )
        c.set_path_effects([pe.SimpleLineShadow((1, 0)), pe.Normal()])
        ax.scatter(
            self.vxs[smallcities, 0],
            self.vxs[smallcities, 1],
            c="black",
            s=30,
            zorder=15,
            edgecolor="none",
        )

        # Draw city labels
        labelbox = dict(boxstyle="round,pad=0.1", fc="white", ec="none")
        for city in self.cities:
            if city in bigcities:
                size = "small"
                ytext = 12
                stroke_lw = 2
                zorder = 30
            else:
                size = "x-small"
                ytext = 8
                stroke_lw = 2
                zorder = 30

            txt = ax.annotate(
                xy=self.vxs[city, :],
                text=self.city_names[city],
                xytext=(0, ytext),
                ha="center",
                va="center",
                textcoords="offset points",
                # bbox=labelbox,
                size=size,
                zorder=zorder,
            )
            # Add stroke to text label
            txt.set_path_effects([pe.Stroke(linewidth=stroke_lw, foreground="lightsteelblue"), pe.Normal()])

        # Draw region labels
        reglabels = []
        for terr in sorted(
            np.unique(self.territories),
            key=lambda t: np.sum((self.territories == t) & (self.elevation[:-1] > 0)),
        ):
            name = self.region_names[terr]
            w = 0.06 + 0.015 * len(name)
            region = self.territories == terr
            landregion = region & (self.elevation[:-1] > 0)
            scores = np.zeros(self.nvxs)
            center = np.mean(self.vxs[region, :], 0)
            landcenter = np.mean(self.vxs[landregion, :], 0)
            landradius = np.mean(landregion) ** 0.5
            scores = -5000 * distance(self.vxs, landcenter)
            scores -= 1000 * distance(self.vxs, center)
            scores[~region] -= 3000

            for city in self.cities:
                dists = self.vxs - self.vxs[city, :] - np.array([[0, 0.02]])
                exclude = (np.abs(dists[:, 0]) < w) & (np.abs(dists[:, 1]) < 0.05)
                scores[exclude] -= 4000 if city in bigcities else 500

            for rl in reglabels:
                dists = self.vxs - rl
                exclude = (np.abs(dists[:, 0]) < 0.15 + w) & (np.abs(dists[:, 1]) < 0.1)
                scores[exclude] -= 5000
            
            scores[self.elevation[:-1] <= 0] -= 500
            scores[self.vxs[:, 0] > 1.06 - w] -= 50000
            scores[self.vxs[:, 0] < w - 0.06] -= 50000
            scores[self.vxs[:, 1] > 0.97] -= 50000
            scores[self.vxs[:, 1] < 0.03] -= 50000
            assert scores.max() > -50000

            xy = self.vxs[np.argmax(scores), :]
            # ax.axvspan(xy[0] - w, xy[0] + w, xy[1] - 0.07, xy[1] + 0.03,
            # facecolor='none', edgecolor='red', zorder=19)
            print(f"Labelling {name} at {scores.max():.1f}")
            reglabels.append(xy)
            label = name

            txt = ax.annotate(
                xy=xy,
                text= label,
                ha="center",
                va="center",
                # bbox=labelbox,
                size="large",
                zorder=30,
            )
            txt.set_path_effects([pe.Stroke(linewidth=3, foreground="slategrey"), pe.Normal()])

        # Make borders and coasts
        borders = []
        # borderadj = defaultdict(list)
        coasts = []
        for rv, rp in zip(self.vor.ridge_vertices, self.vor.ridge_points):
            if -1 in rv or -1 in rp:
                continue
            v1, v2 = rv
            p1, p2 = rp
            if not (good[v1] and good[v2]):
                continue
            if self.territories[v1] != self.territories[v2] and self.elevation[v1] > 0 and self.elevation[v2] > 0:
                borders.append((p1, p2))
            if (self.elevation[v1] > 0 and self.elevation[v2] <= 0) or (
                self.elevation[v2] > 0 and self.elevation[v1] <= 0
            ):
                coasts.append(self.pts[rp, :])

        # Draw borders
        borders = mergelines(relaxpts(self.pts, borders))
        print("Borders:", len(borders))
        bordercol = mpl.collections.PathCollection(borders)
        bordercol.set_facecolor("none")
        bordercol.set_edgecolor("black")
        bordercol.set_linestyle("--")
        bordercol.set_linewidth(1.0)
        bordercol.set_zorder(13)
        ax.add_collection(bordercol)

        # Draw coasts
        coastcol = mpl.collections.PathCollection(mergelines(coasts))
        coastcol.set_facecolor("none")
        coastcol.set_edgecolor("black")
        coastcol.set_zorder(11)
        coastcol.set_linewidth(1.0)
        ax.add_collection(coastcol)

        print("Main Road")
        print(self.big_cities)
        clist = self.ordered_cities(self.big_cities)
        #smallcities = [c for c in self.cities if c not in bigcities]
        for c in clist:
            print(c)
        clist = ["topleft"] + list(clist) + ["bottomright"]
        for c1, c2 in zip(clist[:-1], clist[1:]):
            print(f"Path between {c1} and {c2}")
            path, _ = self.shortest_path(c1, c2)

            #ax.add_collection(pathcol)
            ax.plot(self.vxs[path, 0], self.vxs[path, 1], c="red", zorder=20, linewidth=3, alpha=0.5)


        #print("Small Roads")
        #smallcities = [c for c in self.cities if c not in self.big_cities]
        #print(smallcities)
        #print(self.big_cities)

        #for bigcity in self.big_cities:
        #    regioncity = []
        #    noregioncity = []
        #    for smallcity in smallcities:
        #        if self.territories[smallcity] == bigcity:
        #            regioncity.append(smallcity)
        #        else:
        #            noregioncity.append(smallcity)
        #    regioncity.append(bigcity)
        #    print(regioncity)
        #    #clist = self.ordered_cities_region(regioncity)
        #    clist = []
        #    for city in regioncity:
        #        if city < 2 ** sizemap:
        #            clist.append(city)
        #    clist = clist + bigcity
        #    print(clist)

        #    for c1, c2 in zip(clist[:-1], clist[1:]):
        #        print(f"Path between {c1} and {c2}")
        #        path, _ = self.shortest_path(c1, c2)
        #        ax.plot(self.vxs[path, 0], self.vxs[path, 1], c="red", zorder=10000, linewidth=1, alpha=0.5)

        ax.axis("image")
        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(0.0, 1.0)
        # plt.xticks(np.arange(0, 21) * .05)
        # plt.yticks(np.arange(0, 21) * .05)
        # plt.grid(True)
        ax.axis("off")

        plt.savefig(filename, **kwargs)
        plt.close()

    def name_places(self):
        """Generate place names using lang module."""
        self.city_names = {}
        self.region_names = {}
        for city in self.cities:
            self.city_names[city] = self.lang.name("city")
        for region in np.unique(self.territories):
            self.region_names[region] = self.lang.name("region")


# m = MapGrid(16384)
# pickle.dump(m, open("map.pickle", "w"))

# m = pickle.load(open("map.pickle"))
# m.plot()
# plt.savefig("test.png", dpi=150)


if __name__ == "__main__":
    for i in range(1):
        plt.close("all")
        while True:
            try:
                m = MapGrid(n=2 ** sizemap)
                filename = f"images/{m.mode}-{i:02d}.png"
                m.plot(filename, dpi=200)
                break
            except AssertionError:
                print("Failed assertion, retrying")
