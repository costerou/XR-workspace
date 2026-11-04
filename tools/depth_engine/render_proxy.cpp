// Stand-in for the XR renderer in the contention test: EGL surfaceless GLES on the dGPU, one full-screen pass per
// 8.33 ms tick, reporting how long each frame took and how many went over the 120 Hz budget. Touches no display.
// Usage: DRI_PRIME=pci-0000_03_00_0 render_proxy [seconds]
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl32.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>
using C=std::chrono::steady_clock;
int main(int c,char**v){ double secs=c>1?atof(v[1]):5;
 auto gpd=(PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
 EGLDisplay d=gpd(EGL_PLATFORM_SURFACELESS_MESA,EGL_DEFAULT_DISPLAY,nullptr); eglInitialize(d,0,0); eglBindAPI(EGL_OPENGL_ES_API);
 EGLConfig cf; EGLint n; EGLint ca[]={EGL_RENDERABLE_TYPE,EGL_OPENGL_ES3_BIT,EGL_NONE}; eglChooseConfig(d,ca,&cf,1,&n);
 std::vector<EGLint> a={EGL_CONTEXT_MAJOR_VERSION,3,EGL_CONTEXT_MINOR_VERSION,2}; a.push_back(EGL_NONE);
 EGLContext x=eglCreateContext(d,EGL_NO_CONFIG_KHR,EGL_NO_CONTEXT,a.data()); eglMakeCurrent(d,EGL_NO_SURFACE,EGL_NO_SURFACE,x);
 fprintf(stderr,"%s\n",glGetString(GL_RENDERER));
 const char*vs="#version 320 es\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0,1);}";
 const char*fs="#version 320 es\nprecision highp float;out vec4 o;uniform float t;void main(){vec2 u=gl_FragCoord.xy/1200.;float s=0.;for(int i=0;i<160;i++)s+=sin(u.x*float(i)+t)*cos(u.y*float(i));o=vec4(s,s,s,1);}";
 auto sh=[&](GLenum k,const char*s){GLuint o=glCreateShader(k);glShaderSource(o,1,&s,0);glCompileShader(o);return o;};
 GLuint p=glCreateProgram();glAttachShader(p,sh(GL_VERTEX_SHADER,vs));glAttachShader(p,sh(GL_FRAGMENT_SHADER,fs));glLinkProgram(p);glUseProgram(p);
 GLuint tx,fb,va;glGenTextures(1,&tx);glBindTexture(GL_TEXTURE_2D,tx);glTexStorage2D(GL_TEXTURE_2D,1,GL_RGBA8,3840,1200);glGenFramebuffers(1,&fb);glBindFramebuffer(GL_FRAMEBUFFER,fb);glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,tx,0);glGenVertexArrays(1,&va);glBindVertexArray(va);glViewport(0,0,3840,1200);
 std::vector<double> t; auto start=C::now(),next=start; auto per=std::chrono::nanoseconds(8333333); int late=0;
 while(std::chrono::duration<double>(C::now()-start).count()<secs){ auto t0=C::now(); glUniform1f(glGetUniformLocation(p,"t"),t.size()*0.01f);glDrawArrays(GL_TRIANGLES,0,3);glFinish();auto t1=C::now();t.push_back(std::chrono::duration<double,std::milli>(t1-t0).count()); next+=per; if(C::now()>next){late++;next=C::now();} else std::this_thread::sleep_until(next);}
 std::sort(t.begin(),t.end()); int over=std::count_if(t.begin(),t.end(),[](double a){return a>8.33;});
 printf("frames %zu p50 %.2f p95 %.2f p99 %.2f max %.2f ms; over 8.33 ms: %d (%.1f%%), late ticks %d\n",t.size(),t[t.size()/2],t[t.size()*95/100],t[t.size()*99/100],t.back(),over,100.*over/t.size(),late);}
