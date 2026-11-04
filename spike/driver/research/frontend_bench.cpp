// What the image half of an open VIO costs on this CPU, from synthetic frames. Touches no camera.
//
// One core, one stereo pair per iteration: track N corners left-to-left over time, match left-to-right,
// and top up with new corners. That is what Basalt-style sparse front ends do each frame. It leaves out
// the optimiser, so read the result as a floor for a whole tracker, never its total. Resolution is a
// parameter because the Luma Ultra's tracking camera resolution is not public.
//
// Build:  c++ -O2 -std=c++20 frontend_bench.cpp -o frontend_bench -I/usr/include/opencv5 -lopencv_core -lopencv_imgproc -lopencv_features -lopencv_video
// Run:    ./frontend_bench 640 480 150 400
#include <opencv2/core.hpp>
#include <opencv2/features.hpp>
#include <opencv2/imgproc.hpp>
#include <opencv2/video/tracking.hpp>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <vector>

static cv::Mat scene(int width, int height) {
    cv::Mat noise(height * 2, width * 2, CV_8U), out;
    cv::randu(noise, 0, 255);
    cv::GaussianBlur(noise, out, cv::Size(), 2.5);
    cv::normalize(out, out, 0, 255, cv::NORM_MINMAX);
    return out;
}

int main(int argc, char **argv) {
    const int width = argc > 1 ? atoi(argv[1]) : 640, height = argc > 2 ? atoi(argv[2]) : 480;
    const int corners = argc > 3 ? atoi(argv[3]) : 150, frames = argc > 4 ? atoi(argv[4]) : 400;
    cv::setNumThreads(1);
    cv::Mat world = scene(width, height), previous;
    std::vector<cv::Point2f> points;
    std::vector<double> times;
    const cv::Size window(21, 21);
    for (int i = 0; i < frames; i++) {
        const int x = width / 2 + (i * 3) % (width / 2), y = height / 2 + (i * 2) % (height / 2);
        cv::Mat left = world(cv::Rect(x - width / 2 + 8, y - height / 2, width, height)).clone();
        cv::Mat right = world(cv::Rect(x - width / 2, y - height / 2, width, height)).clone();
        const auto start = std::chrono::steady_clock::now();
        std::vector<uchar> status;
        std::vector<float> error;
        if (!points.empty()) {
            std::vector<cv::Point2f> moved;
            cv::calcOpticalFlowPyrLK(previous, left, points, moved, status, error, window, 3);
            std::vector<cv::Point2f> kept;
            for (size_t k = 0; k < moved.size(); k++)
                if (status[k] && moved[k].x > 0 && moved[k].y > 0 && moved[k].x < width && moved[k].y < height) kept.push_back(moved[k]);
            points.swap(kept);
        }
        if (static_cast<int>(points.size()) < corners) {
            std::vector<cv::Point2f> fresh;
            cv::goodFeaturesToTrack(left, fresh, corners - static_cast<int>(points.size()), 0.01, 12);
            points.insert(points.end(), fresh.begin(), fresh.end());
        }
        if (!points.empty()) {
            std::vector<cv::Point2f> stereo;
            cv::calcOpticalFlowPyrLK(left, right, points, stereo, status, error, window, 3);
        }
        times.push_back(std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count());
        previous = left;
    }
    std::sort(times.begin(), times.end());
    double total = 0;
    for (double t : times) total += t;
    const double mean = total / times.size();
    printf("%dx%d %d corners: mean %.2f ms, p99 %.2f ms; at 25 Hz %.1f%% of one core, at 30 Hz %.1f%%\n", width, height, corners, mean,
           times[times.size() * 99 / 100], mean * 2.5, mean * 3.0);
}
