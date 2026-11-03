// Host benchmark for the vhsnunzip Coyote harness (harness/hw).
//
// Streams every chunk of a corpus (harness/scripts/make_corpus.py) through the
// vFPGA as one LOCAL_TRANSFER (compressed in, decompressed out), keeping up to
// --depth chunks in flight, and reads the cycle counters of the harness after
// each repetition. Builds against the real libcoyote or, with -DEN_SIM=ON,
// against the Coyote simulation (set COYOTE_SIM_DIR to the hw sim build).

#include <getopt.h>
#include <unistd.h>

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include <coyote/cThread.hpp>

#include "regs.hpp"

namespace {

struct Chunk {
    std::vector<uint8_t> comp, raw;
};

struct Corpus {
    std::string        path;
    std::string        header; // JSON object written by make_corpus.py
    std::vector<Chunk> chunks;
};

struct Args {
    std::vector<std::string> corpora;
    int                      reps     = 5;
    size_t                   depth    = 16;
    bool                     verify   = false;
    uint32_t                 in_mask  = 0;
    uint32_t                 out_mask = 0;
    std::string              out_dir;
    std::string              build_config;
#ifdef EN_SIM
    bool hpf = false;
#else
    bool hpf = true;
#endif
};

struct Info {
    unsigned    c_bytes, dual_issue, long_chunks;
    std::string ram_style;
};

using Counters = std::map<std::string, uint64_t>;

struct Rep {
    double   wall_s;
    uint64_t ticks;
    Counters counters;
};

uint32_t read_u32(std::ifstream &f) {
    uint8_t b[4];
    f.read(reinterpret_cast<char *>(b), 4);
    return b[0] | (b[1] << 8) | (b[2] << 16) | (uint32_t(b[3]) << 24);
}

Corpus read_corpus(const std::string &path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("cannot open " + path);
    char magic[8];
    f.read(magic, 8);
    if (!f || std::memcmp(magic, "VSZCORP1", 8) != 0) {
        throw std::runtime_error(path + " is not a vhsnunzip corpus");
    }
    Corpus c;
    c.path = path;
    c.header.resize(read_u32(f));
    f.read(&c.header[0], c.header.size());
    c.chunks.resize(read_u32(f));
    for (auto &chunk : c.chunks) {
        chunk.comp.resize(read_u32(f));
        chunk.raw.resize(read_u32(f));
        f.read(reinterpret_cast<char *>(chunk.comp.data()), chunk.comp.size());
        f.read(reinterpret_cast<char *>(chunk.raw.data()), chunk.raw.size());
    }
    if (!f) throw std::runtime_error(path + " is truncated");
    return c;
}

// First integer value of "key" in a flat search of a JSON text (good enough for
// the corpus header, whose keys are unique)
uint64_t json_uint(const std::string &json, const std::string &key) {
    auto pos = json.find("\"" + key + "\":");
    if (pos == std::string::npos) return 0;
    return std::strtoull(json.c_str() + pos + key.size() + 3, nullptr, 10);
}

std::string json_string_value(const std::string &json, const std::string &key) {
    auto pos = json.find("\"" + key + "\": \"");
    if (pos == std::string::npos) return "";
    pos += key.size() + 5;
    return json.substr(pos, json.find('"', pos) - pos);
}

std::string read_file(const std::string &path) {
    std::ifstream     f(path);
    std::stringstream s;
    s << f.rdbuf();
    if (!f) throw std::runtime_error("cannot read " + path);
    return s.str();
}

std::string to_json(const Counters &c) {
    std::ostringstream s;
    s << "{";
    for (auto it = c.begin(); it != c.end(); ++it) {
        s << (it == c.begin() ? "" : ", ") << "\"" << it->first << "\": " << it->second;
    }
    s << "}";
    return s.str();
}

Counters read_counters(coyote::cThread &t) {
    Counters c;
    for (const auto &counter : vsz::COUNTERS) c[counter.name] = t.getCSR(counter.reg);
    return c;
}

Info read_info(coyote::cThread &t) {
    uint64_t v = t.getCSR(vsz::REG_INFO);
    if ((v >> 32) != vsz::INFO_MAGIC) {
        char buf[64];
        std::snprintf(buf, sizeof(buf), "INFO register is 0x%016llx", (unsigned long long)v);
        throw std::runtime_error(std::string(buf) + ": the vFPGA is not the vhsnunzip harness");
    }
    return {unsigned(v & 0xFF), unsigned((v >> 8) & 1), unsigned((v >> 9) & 1),
            (v >> 10) & 1 ? "block" : "ultra"};
}

void *alloc(coyote::cThread &t, size_t size, bool &hpf) {
    if (hpf) {
        void *p = t.getMem({coyote::CoyoteAllocType::HPF, size});
        if (p) return p;
        std::cerr << "warning: huge page allocation failed, using regular pages" << std::endl;
        hpf = false;
    }
    void *p = t.getMem({coyote::CoyoteAllocType::REG, size});
    if (!p) throw std::runtime_error("memory allocation failed");
    return p;
}

size_t align64(size_t n) { return (n + 63) & ~size_t(63); }

double median(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    return v.empty() ? 0.0 : v[v.size() / 2];
}

// Runs one corpus; returns false if the verification failed
bool run_corpus(coyote::cThread &t, const Corpus &corpus, const Info &info, Args &args) {
    std::string name = json_string_value(corpus.header, "name");
    if (name.empty()) name = corpus.path;

    // Chunks that this build can run: Coyote cannot write 0 bytes, and chunks
    // over 64 KiB need LONG_CHUNKS
    std::vector<size_t> use, in_off, out_off;
    size_t              in_size = 0, out_size = 0, comp_bytes = 0, raw_bytes = 0;
    for (size_t i = 0; i < corpus.chunks.size(); i++) {
        const auto &c = corpus.chunks[i];
        if (c.raw.empty() || (c.raw.size() > 65536 && !info.long_chunks) ||
            c.raw.size() > coyote::MAX_TRANSFER_SIZE) {
            continue;
        }
        use.push_back(i);
        in_off.push_back(in_size);
        out_off.push_back(out_size);
        in_size += align64(c.comp.size());
        out_size += align64(c.raw.size());
        comp_bytes += c.comp.size();
        raw_bytes += c.raw.size();
    }
    if (use.size() != corpus.chunks.size()) {
        std::cerr << name << ": skipping " << corpus.chunks.size() - use.size()
                  << " empty or unsupported chunks" << std::endl;
    }
    if (use.empty()) return true;

    auto *in  = static_cast<uint8_t *>(alloc(t, in_size, args.hpf));
    auto *out = static_cast<uint8_t *>(alloc(t, out_size, args.hpf));
    std::vector<coyote::localSg> src(use.size()), dst(use.size());
    for (size_t k = 0; k < use.size(); k++) {
        const auto &c = corpus.chunks[use[k]];
        std::memcpy(in + in_off[k], c.comp.data(), c.comp.size());
        src[k].addr = in + in_off[k];
        src[k].len  = c.comp.size();
        dst[k].addr = out + out_off[k];
        dst[k].len  = c.raw.size();
    }

    t.setCSR(args.in_mask | (args.out_mask << 16), vsz::REG_THROTTLE);

    std::vector<Rep> reps;
    std::string      verify = args.verify ? "ok" : "skipped";
    for (int rep = 0; rep < args.reps; rep++) {
        if (args.verify && rep == 0) std::memset(out, 0, out_size);
        t.setCSR(1, vsz::REG_CTRL);
        t.clearCompleted();

        uint64_t ts0 = t.getCSR(vsz::REG_TIMESTAMP);
        auto     w0  = std::chrono::steady_clock::now();
        size_t   issued = 0, done = 0;
        while (done < use.size()) {
            for (; issued < use.size() && issued - done < args.depth; issued++) {
                t.invoke(coyote::CoyoteOper::LOCAL_TRANSFER, src[issued], dst[issued]);
            }
            done = t.checkCompleted(coyote::CoyoteOper::LOCAL_TRANSFER);
        }
        auto     w1  = std::chrono::steady_clock::now();
        uint64_t ts1 = t.getCSR(vsz::REG_TIMESTAMP);
        reps.push_back({std::chrono::duration<double>(w1 - w0).count(), ts1 - ts0, read_counters(t)});

        if (args.verify && rep == 0) {
            for (size_t k = 0; k < use.size() && verify == "ok"; k++) {
                const auto &raw = corpus.chunks[use[k]].raw;
                if (std::memcmp(out + out_off[k], raw.data(), raw.size()) != 0) {
                    size_t b = 0;
                    while (out[out_off[k] + b] == raw[b]) b++;
                    verify = "failed: chunk " + std::to_string(use[k]) + " byte " + std::to_string(b);
                }
            }
        }
    }
    t.freeMem(in);
    t.freeMem(out);

    // Totals over all repetitions
    Counters total;
    std::vector<double> walls;
    uint64_t ticks = 0;
    double   wall  = 0;
    for (const auto &r : reps) {
        for (const auto &kv : r.counters) total[kv.first] += kv.second;
        walls.push_back(r.wall_s);
        ticks += r.ticks;
        wall += r.wall_s;
    }
    total["in_flight"] = reps.back().counters["in_flight"];
    uint64_t class_sum = 0;
    for (auto cls : vsz::CYCLE_CLASSES) class_sum += total[cls];

    double cycles   = std::max<double>(total["cycles"], 1);
    double clk_mhz  = wall > 0 ? ticks / wall / 1e6 : 0;
    double out_bpc  = total["de_bytes"] / cycles;
    double elements = json_uint(corpus.header, "elements");
    double host_gbs = raw_bytes / median(walls) / 1e9;

    std::printf("%-20s %5zu chunks %9.3f MB  ratio %5.2f | %6.3f B/cycle (%3.0f%%)  %5.3f elem/cycle"
                " | core_stall %4.1f%% in_empty %4.1f%% in_only %4.1f%% de_blocked %4.1f%%"
                " | clk %6.1f MHz  core %5.2f GB/s  host %5.2f GB/s | verify %s\n",
                name.c_str(), use.size(), raw_bytes / 1e6, double(raw_bytes) / comp_bytes,
                out_bpc, 100 * out_bpc / info.c_bytes, elements / cycles * args.reps,
                100 * total["core_stall"] / cycles, 100 * total["in_empty"] / cycles,
                100 * total["in_only"] / cycles, 100 * total["de_blocked"] / cycles, clk_mhz,
                out_bpc * clk_mhz / 1e3, host_gbs, verify.c_str());
    if (class_sum != total["cycles"] || total["de_bytes"] != raw_bytes * reps.size()) {
        std::cerr << name << ": warning: counters are inconsistent (cycle classes " << class_sum
                  << " vs cycles " << total["cycles"] << ", de_bytes " << total["de_bytes"]
                  << " vs " << raw_bytes * reps.size() << ")" << std::endl;
    }

    if (!args.out_dir.empty()) {
        std::string file = name;
        for (auto &ch : file) {
            if (!std::isalnum(static_cast<unsigned char>(ch)) && ch != '-') ch = '_';
        }
        std::string   path = args.out_dir + "/hw_" + (info.dual_issue ? "dual_" : "single_") + file + ".json";
        std::ofstream f(path);
        f << "{\n  \"kind\": \"hw\",\n  \"config\": {\"c_bytes\": " << info.c_bytes
          << ", \"dual_issue\": " << info.dual_issue << ", \"long_chunks\": " << info.long_chunks
          << ", \"ram_style\": \"" << info.ram_style << "\", \"clk_mhz_measured\": " << clk_mhz;
        if (!args.build_config.empty()) f << ", \"build\": " << read_file(args.build_config);
        f << "},\n  \"corpus\": " << corpus.header << ",\n  \"run\": {\"reps\": " << reps.size()
          << ", \"depth\": " << args.depth << ", \"in_mask\": " << args.in_mask
          << ", \"out_mask\": " << args.out_mask << ", \"chunks_used\": " << use.size()
          << ", \"raw_bytes\": " << raw_bytes << ", \"comp_bytes\": " << comp_bytes
          << ", \"wall_s_median\": " << median(walls) << ", \"host_gbps\": " << host_gbs
          << ", \"verify\": \"" << verify << "\"},\n  \"counters\": " << to_json(total)
          << ",\n  \"reps\": [";
        for (size_t r = 0; r < reps.size(); r++) {
            f << (r ? ",\n    " : "\n    ") << "{\"wall_s\": " << reps[r].wall_s
              << ", \"ticks\": " << reps[r].ticks << ", \"counters\": " << to_json(reps[r].counters) << "}";
        }
        f << "\n  ]\n}\n";
        if (!f) throw std::runtime_error("cannot write " + path);
    }
    return verify.rfind("failed", 0) != 0;
}

void usage(const char *prog) {
    std::cerr << "Usage: " << prog << " [options] CORPUS...\n"
              << "  -r, --reps N           repetitions per corpus (default 5)\n"
              << "  -d, --depth N          chunks in flight (default 16; 1 samples the latency of every chunk)\n"
              << "  -v, --verify           check the output of the first repetition\n"
              << "  -t, --throttle IN,OUT  input/output gate masks, e.g. 0x3,0 (default 0,0)\n"
              << "  -o, --out DIR          write one JSON result per corpus to DIR\n"
              << "  -b, --build-config F   harness_config.json of the bitstream's build, stored in the results\n"
              << "  -m, --mem hpf|reg      host buffer pages (default hpf; reg in simulation)\n";
}

Args parse_args(int argc, char *argv[]) {
    static option long_options[] = {
        {"reps", required_argument, 0, 'r'},     {"depth", required_argument, 0, 'd'},
        {"verify", no_argument, 0, 'v'},         {"throttle", required_argument, 0, 't'},
        {"out", required_argument, 0, 'o'},      {"build-config", required_argument, 0, 'b'},
        {"mem", required_argument, 0, 'm'},      {"help", no_argument, 0, 'h'},
        {0, 0, 0, 0}};
    Args args;
    int  opt;
    while ((opt = getopt_long(argc, argv, "r:d:vt:o:b:m:h", long_options, nullptr)) != -1) {
        switch (opt) {
        case 'r': args.reps = std::stoi(optarg); break;
        case 'd': args.depth = std::stoul(optarg); break;
        case 'v': args.verify = true; break;
        case 't': {
            std::string s(optarg);
            auto        comma = s.find(',');
            args.in_mask      = std::stoul(s.substr(0, comma), nullptr, 0) & 0xFFFF;
            if (comma != std::string::npos) args.out_mask = std::stoul(s.substr(comma + 1), nullptr, 0) & 0xFFFF;
            break;
        }
        case 'o': args.out_dir = optarg; break;
        case 'b': args.build_config = optarg; break;
        case 'm': args.hpf = std::string(optarg) == "hpf"; break;
        case 'h': usage(argv[0]); std::exit(0);
        default: usage(argv[0]); std::exit(2);
        }
    }
    for (int i = optind; i < argc; i++) args.corpora.push_back(argv[i]);
    if (args.corpora.empty() || args.reps < 1 || args.depth < 1) {
        usage(argv[0]);
        std::exit(2);
    }
    return args;
}

} // namespace

int main(int argc, char *argv[]) {
    Args args = parse_args(argc, argv);
    try {
        coyote::cThread t(0, getpid());
        Info info = read_info(t);
        std::cout << "vhsnunzip harness: C_BYTES=" << info.c_bytes << " DUAL_ISSUE=" << info.dual_issue
                  << " LONG_CHUNKS=" << info.long_chunks << " RAM=" << info.ram_style
                  << " | reps " << args.reps << ", depth " << args.depth << std::endl;
        bool ok = true;
        for (const auto &path : args.corpora) ok &= run_corpus(t, read_corpus(path), info, args);
        return ok ? EXIT_SUCCESS : EXIT_FAILURE;
    } catch (const std::exception &e) {
        std::cerr << "error: " << e.what() << std::endl;
        return EXIT_FAILURE;
    }
}
