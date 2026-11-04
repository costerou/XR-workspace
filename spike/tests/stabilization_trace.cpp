// Offline stdin: device_ms x y z w; stdout: time, then x y z w and prediction
// gain for Off/Light/Medium/Strong. Uses the exact renderer filter implementation.
#include "xr/stabilization.hpp"
#include <iostream>
#include <iomanip>
int main() {
    xr::Stabilizer filters[4]; int strengths[]{0,25,60,100};
    for(int i=0;i<4;++i) filters[i].strength(strengths[i]);
    double t; xr::Quat q;
    std::cout << std::setprecision(10);
    while(std::cin >> t >> q.x >> q.y >> q.z >> q.w) {
        std::cout << t;
        for(auto& f : filters) {
            const auto out=f.update(q,t);
            std::cout << ' ' << out.x << ' ' << out.y << ' ' << out.z << ' ' << out.w << ' ' << f.prediction_gain();
        }
        std::cout << '\n';
    }
    return std::cin.eof() ? 0 : 1;
}
