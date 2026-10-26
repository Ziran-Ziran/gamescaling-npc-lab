// Independent causal adapter. Existing ORB-SLAM3 sources and library are unchanged.
#include "System.h"
#include "Map.h"
#include "MapPoint.h"
#include "KeyFrame.h"
#include <opencv2/imgcodecs.hpp>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <set>
#include <sstream>
#include <thread>
#ifdef GSV_DIAGNOSTICS
#include "diagnostics.h"
#endif

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
double ms(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration<double, std::milli>(b-a).count();
}

void matrix(std::ostream& out, const Eigen::Matrix4f& value) {
    out << '[';
    for (int r=0; r<4; ++r) for (int c=0; c<4; ++c) {
        if (r || c) out << ',';
        out << value(r,c);
    }
    out << ']';
}

// Called only after feeding stops. This install's Shutdown does not join workers;
// these are per-map locked snapshots, not a claimed quiescent final atlas.
void snapshots(ORB_SLAM3::System& slam, const std::set<ORB_SLAM3::Map*>& maps,
               const fs::path& out) {
    std::ofstream points(out / "sparse_snapshot.jsonl");
    std::ofstream keys(out / "keyframes_snapshot.jsonl");
    points << std::setprecision(9);
    keys << std::setprecision(9);
    size_t keyCount = 0;
    for (auto* map : maps) {
        std::unique_lock<std::mutex> lock(map->mMutexMapUpdate);
        const auto id = map->GetId();
        for (auto* point : map->GetAllMapPoints()) {
            if (!point || point->isBad()) continue;
            const auto p = point->GetWorldPos();
            if (!p.allFinite()) continue;
            points << "{\"map_id\":" << id << ",\"point_id\":" << point->mnId
                   << ",\"xyz_m\":[" << p.x() << ',' << p.y() << ',' << p.z() << "]}\n";
        }
        for (auto* key : map->GetAllKeyFrames()) {
            if (!key || key->isBad()) continue;
            ++keyCount;
            keys << "{\"map_id\":" << id << ",\"keyframe_id\":" << key->mnId
                 << ",\"t_sensor_s\":" << key->mTimeStamp << ",\"Twc\":";
            matrix(keys, key->GetPoseInverse().matrix());
            keys << "}\n";
        }
    }
    // Upstream TUM writer concatenates histories without map IDs; do not invoke
    // on multiple maps or no keyframes (vpKFs[0] is unchecked in local source).
    if (maps.size() == 1 && keyCount > 0)
        slam.SaveTrajectoryTUM((out / "trajectory_async_snapshot.tum").string());
}

int run(int argc, char** argv) {
    if (argc != 5 && argc != 6) {
        std::cerr << "usage: gsv_rgbd VOCAB SETTINGS INPUT NEW_OUTPUT [pace_hz=0]\n";
        return 1;
    }
    try {
        const fs::path input = fs::canonical(argv[3]);
        const fs::path out = fs::absolute(argv[4]);
        const double pace = argc == 6 ? std::stod(argv[5]) : 0.;
        if (!std::isfinite(pace) || pace < 0) throw std::runtime_error("invalid pace");
        for (const auto& entry : fs::directory_iterator(input)) {
            const auto name = entry.path().filename().string();
            if (name != "frames" && name != "depth" && name != "sensor.json" &&
                name != "frames.tsv" && name != "settings.yaml")
                throw std::runtime_error("non-sensor file in input: " + name);
            if (entry.is_symlink()) throw std::runtime_error("input symlinks forbidden");
        }
        if (!fs::create_directory(out)) throw std::runtime_error("output already exists");
        std::ifstream manifest(input / "frames.tsv");
        if (!manifest) throw std::runtime_error("missing manifest");
        cv::FileStorage settings(argv[2], cv::FileStorage::READ);
        if (!settings.isOpened() || settings["Camera.RGB"].empty() ||
            settings["loopClosing"].empty() || settings["RGBD.DepthMapFactor"].empty() ||
            (int)settings["Camera.RGB"] != 0 || (int)settings["loopClosing"] != 0 ||
            (double)settings["RGBD.DepthMapFactor"] != 1000.)
            throw std::runtime_error("BGR, loopClosing=0, depth factor 1000 required");
        const int width = (int)settings["Camera.width"], height = (int)settings["Camera.height"];
        cv::setNumThreads(4);
        std::cout << "adapter: Viewer=false loopClosing=0 BGR8 depth_mm_factor=1000 "
                  << "time_source=nominal_frame_id_over_target_hz pace_hz=" << pace << std::endl;
#ifdef GSV_DIAGNOSTICS
        gsvdiag::start((out / "diagnostics.jsonl").string());
#endif
        // This upstream build does not join workers in Shutdown. Keep its
        // objects alive until main terminates the process after flushing files.
        auto* processSystem = new ORB_SLAM3::System(argv[1], argv[2], ORB_SLAM3::System::RGBD, false);
        auto& slam = *processSystem;
        std::ofstream records(out / "tracking.jsonl");
        records << std::setprecision(12);
        std::set<ORB_SLAM3::Map*> maps;
        auto start = Clock::now();
        uint64_t frame, count=0;
        uint64_t previousFrame=0;
        double stamp, previous=-1.;
        std::string colorPath, depthPath, line;
        while (std::getline(manifest, line)) {
            std::istringstream row(line);
            if (!(row >> frame >> stamp >> colorPath >> depthPath) || !std::isfinite(stamp) ||
                stamp <= previous || (count && frame <= previousFrame))
                throw std::runtime_error("bad or non-increasing manifest timestamp");
            previous = stamp;
            previousFrame = frame;
            if (pace > 0) std::this_thread::sleep_until(start +
                std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(count/pace)));
            const auto begin = Clock::now();
            auto checkedPath = [&](const std::string& rel, const std::string& folder) {
                const auto path = fs::path(rel);
                if (path.is_absolute() || path.parent_path() != folder ||
                    fs::is_symlink(input / path) || path.extension() != ".png")
                    throw std::runtime_error("non-sensor image path");
                return (input / path).string();
            };
            const cv::Mat rgb = cv::imread(checkedPath(colorPath, "frames"), cv::IMREAD_UNCHANGED);
            const cv::Mat depth = cv::imread(checkedPath(depthPath, "depth"), cv::IMREAD_UNCHANGED);
            if (rgb.type()!=CV_8UC3 || depth.type()!=CV_16UC1 ||
                rgb.cols!=width || rgb.rows!=height || rgb.size()!=depth.size())
                throw std::runtime_error("RGB-D type/size mismatch");
            const auto loaded = Clock::now();
#ifdef GSV_DIAGNOSTICS
            gsvdiag::beginFrame(frame, stamp);
#endif
            auto Tcw = slam.TrackRGBD(rgb, depth, stamp);
            const auto tracked = Clock::now();
            const int state = slam.GetTrackingState();
            const bool valid = state == ORB_SLAM3::Tracking::OK && Tcw.matrix().allFinite();
            long mapId=-1;
            if (valid) for (auto* point : slam.GetTrackedMapPoints()) {
                if (point && !point->isBad() && point->GetMap()) {
                    auto* map = point->GetMap();
                    mapId = static_cast<long>(map->GetId());
                    maps.insert(map);
                    break;
                }
            }
            records << "{\"frame_id\":" << frame << ",\"t_sensor_s\":" << stamp
                    << ",\"state\":" << state << ",\"valid\":" << (valid ? "true" : "false")
                    << ",\"map_id\":" << mapId << ",\"load_ms\":" << ms(begin,loaded)
                    << ",\"tracking_ms\":" << ms(loaded,tracked)
                    << ",\"wall_elapsed_ms\":" << ms(start,tracked) << ",\"Twc\":";
            if (valid) matrix(records, Tcw.inverse().matrix()); else records << "null";
            records << "}\n";
            records.flush();
#ifdef GSV_DIAGNOSTICS
            gsvdiag::emit("frame_result", {{"state", double(state)}, {"valid", double(valid)},
                                           {"map_id", double(mapId)}});
#endif
            ++count;
            if (count%50 == 0) std::cout << "processed=" << count << " state=" << state << std::endl;
        }
        if (!count) throw std::runtime_error("empty input");
        const auto finished = Clock::now();
        slam.Shutdown();
#ifdef GSV_DIAGNOSTICS
        gsvdiag::stop(); // Freeze log before report/hash; workers may still finish.
#endif
        snapshots(slam, maps, out);
        std::ofstream summary(out / "run.json");
        summary << "{";
#ifdef GSV_DIAGNOSTICS
        summary << "\"instrumented\":true,\"diagnostic_schema\":2,";
#else
        summary << "\"instrumented\":false,";
#endif
        summary << "\"frames\":" << count << ",\"processing_wall_s\":" << ms(start,finished)/1000.
                << ",\"pace_hz\":" << pace << ",\"observed_maps\":" << maps.size()
                << ",\"time_source\":\"nominal_frame_id_over_target_hz\","
                << "\"pose_direction\":\"Twc: CV camera to native map, metres\","
                << "\"map_export\":\"per-map locked asynchronous snapshot; no reload claim\"}\n";
        std::cout << "complete frames=" << count << " maps=" << maps.size() << std::endl;
        return 0;
    } catch (const std::exception& error) {
#ifdef GSV_DIAGNOSTICS
        gsvdiag::stop();
#endif
        std::cerr << "adapter error: " << error.what() << std::endl;
        return 2;
    }
}

int main(int argc, char** argv) {
    const int code = run(argc, argv); // Local output streams close before return.
    std::cout.flush();
    std::cerr.flush();
    // Avoid destroying global ORB mutexes while its unjoined workers may use
    // them. The OS ends every thread of this standalone process together.
    std::_Exit(code);
}
