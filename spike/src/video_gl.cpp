#include "video_gl.hpp"
#include <GLES2/gl2.h>

namespace xr {
namespace {
const char* kVertex = "attribute vec2 pos; varying vec2 ndc; void main() { ndc = pos; gl_Position = vec4(pos, 0.0, 1.0); }\n";
// Mirrors video.cpp video_uv. `view` is uploaded column-major from the row-major world -> eye matrix, so `e * view` is
// viewᵀ·e (eye -> world). highp: 8K frames need sub-texel angular precision.
const char* kFragment =
    "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n"
    "varying vec2 ndc; uniform sampler2D tex; uniform mat3 view; uniform vec2 half_fov; uniform float projection;\n"
    "uniform vec4 rect; uniform float fov; uniform float flip;\n"
    "const float PI = 3.14159265358979;\n"
    "void main() {\n"
    "  vec3 d = normalize(vec3(ndc.x * half_fov.x, ndc.y * half_fov.y, -1.0) * view);\n"
    "  vec2 uv; bool inside = true;\n"
    "  if (projection > 1.5) {\n"
    "    float r = acos(clamp(-d.z, -1.0, 1.0)) / (0.5 * fov);\n"
    "    float l = length(d.xy); vec2 c = l > 1e-7 ? d.xy / l : vec2(0.0);\n"
    "    uv = vec2(0.5 + 0.5 * r * c.x, 0.5 - 0.5 * r * c.y); inside = r <= 1.0;\n"
    "  } else {\n"
    "    float lon = atan(d.x, -d.z); float lat = asin(clamp(d.y, -1.0, 1.0));\n"
    "    if (projection > 0.5) { uv = vec2(0.5 + lon / PI, 0.5 - lat / PI); inside = abs(lon) <= 0.5 * PI + 1e-6; }\n"
    "    else uv = vec2(fract(0.5 + lon / (2.0 * PI)), 0.5 - lat / PI);\n"
    "  }\n"
    "  vec2 t = rect.xy + clamp(uv, 0.0, 1.0) * rect.zw;\n"
    "  if (flip > 0.5) t.y = 1.0 - t.y;\n"
    "  gl_FragColor = inside ? vec4(texture2D(tex, t).rgb, 1.0) : vec4(0.0, 0.0, 0.0, 1.0);\n"
    "}\n";
GLuint compile(GLenum type, const char* source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr); glCompileShader(shader);
    GLint ok = 0; glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) { glDeleteShader(shader); return 0; }
    return shader;
}
}

std::unique_ptr<VideoSphere> VideoSphere::create(std::string& why) {
    std::unique_ptr<VideoSphere> out(new VideoSphere());
    GLuint vs = compile(GL_VERTEX_SHADER, kVertex), fs = compile(GL_FRAGMENT_SHADER, kFragment);
    if (!vs || !fs) { if (vs) glDeleteShader(vs); if (fs) glDeleteShader(fs); why = "video shader compile"; return nullptr; }
    out->program_ = glCreateProgram(); glAttachShader(out->program_, vs); glAttachShader(out->program_, fs); glLinkProgram(out->program_);
    glDeleteShader(vs); glDeleteShader(fs);
    GLint linked = 0; glGetProgramiv(out->program_, GL_LINK_STATUS, &linked);
    if (!linked) { why = "video shader link"; return nullptr; }
    const GLuint p = out->program_;
    out->pos_ = glGetAttribLocation(p, "pos"); out->tex_ = glGetUniformLocation(p, "tex"); out->view_ = glGetUniformLocation(p, "view");
    out->half_ = glGetUniformLocation(p, "half_fov"); out->projection_ = glGetUniformLocation(p, "projection");
    out->rect_ = glGetUniformLocation(p, "rect"); out->fov_ = glGetUniformLocation(p, "fov"); out->flip_ = glGetUniformLocation(p, "flip");
    return out;
}

VideoSphere::~VideoSphere() { if (program_) glDeleteProgram(program_); }

void VideoSphere::draw(unsigned texture, bool flip_y, const VideoFormat& format, int eye, const Mat3& view, float half_w, float half_h) {
    const Mat3 column_major = transpose(view);
    const auto r = eye_rect(format.stereo, eye);
    const float fov = float(format.fov_deg < kFisheyeFovMinDeg ? kFisheyeFovMinDeg : format.fov_deg > kFisheyeFovMaxDeg ? kFisheyeFovMaxDeg
                                                                                                                         : format.fov_deg) * 3.14159265f / 180.f;
    static const GLfloat quad[] = {-1, -1, 1, -1, -1, 1, 1, 1};
    glDisable(GL_BLEND);
    glUseProgram(program_);
    glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, texture);
    glUniform1i(tex_, 0);
    glUniformMatrix3fv(view_, 1, GL_FALSE, column_major.data());
    glUniform2f(half_, half_w, half_h);
    glUniform1f(projection_, float(static_cast<int>(format.projection)));
    glUniform4f(rect_, r.x, r.y, r.w, r.h);
    glUniform1f(fov_, fov);
    glUniform1f(flip_, flip_y ? 1.f : 0.f);
    glVertexAttribPointer(GLuint(pos_), 2, GL_FLOAT, GL_FALSE, 0, quad);
    glEnableVertexAttribArray(GLuint(pos_));
    glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
    glDisableVertexAttribArray(GLuint(pos_));
}
}
