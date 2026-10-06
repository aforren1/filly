// A gltfio TextureProvider for image/webp, built on libwebp. The Filament SDK is built without
// WebP, so its createWebpProvider() returns null. This follows gltfio's own WebpProvider
// (libs/gltfio/src/WebpProvider.cpp in Filament 1.77.1, Apache 2.0): RGBA8 texels, sRGB when
// requested, and a full mip chain from generateMipmaps(). The SDK does not ship the JobSystem
// header that gltfio's provider decodes with, so decoding runs in std::async tasks instead,
// or at once in the single-threaded web build.

#include "webp_provider.h"

#include <filament/Engine.h>
#include <filament/Texture.h>
#include <utils/Log.h>
#include <webp/decode.h>

#include <atomic>
#include <future>
#include <memory>
#include <string>
#include <vector>

namespace filly::detail {
namespace {
namespace f = filament;
namespace g = filament::gltfio;

class WebpProvider final : public g::TextureProvider {
public:
    explicit WebpProvider(f::Engine* engine) : engine_(engine) {}
    ~WebpProvider() override { cancelDecoding(); }

    f::Texture* pushTexture(const uint8_t* data, size_t size, const char*, TextureFlags flags) override {
        int width = 0, height = 0;
        if (!WebPGetInfo(data, size, &width, &height)) {
            push_message_ = "Unable to parse webp texture";
            return nullptr;
        }
        using Format = f::Texture::InternalFormat;
        auto* texture = f::Texture::Builder()
            .width(uint32_t(width)).height(uint32_t(height)).levels(0xff)
            .format(any(flags & TextureFlags::sRGB) ? Format::SRGB8_A8 : Format::RGBA8)
            .usage(f::Texture::Usage::DEFAULT | f::Texture::Usage::GEN_MIPMAPPABLE)
            .build(*engine_);
        if (!texture) {
            push_message_ = "Unable to build Texture object for webp image.";
            return nullptr;
        }
        push_message_.clear();
        auto& info = *textures_.emplace_back(std::make_unique<Info>());
        ++pushed_;
        info.texture = texture;
        info.source.assign(data, data + size);
        auto* job_info = &info;
        auto decode = [job_info] {
            int w = 0, h = 0;
            uint8_t* texels = WebPDecodeRGBA(job_info->source.data(), job_info->source.size(), &w, &h);
            job_info->source.clear();
            job_info->source.shrink_to_fit();
            job_info->texels.store(texels ? intptr_t(texels) : error);
        };
#if defined(__EMSCRIPTEN__)
        // The web build has no threads: decode now. updateQueue() uploads it as usual.
        decode();
#else
        info.job = std::async(std::launch::async, decode);
#endif
        return texture;
    }

    f::Texture* popTexture() override {
        for (auto& info : textures_) {
            if (info->state != State::ready) continue;
            info->state = State::popped;
            ++popped_;
            const intptr_t texels = info->texels.load();
            pop_message_ = texels == error || texels == pending ? "Texture is incomplete" : "";
            return info->texture;
        }
        return nullptr;
    }

    void updateQueue() override {
        for (auto& info : textures_) {
            if (info->state != State::decoding) continue;
            const intptr_t texels = info->texels.load();
            if (texels == pending) continue;
            if (info->job.valid()) info->job.get();
            info->state = State::ready;
            ++decoded_;
            if (texels == error) continue;
            auto* texture = info->texture;
            texture->setImage(*engine_, 0, f::Texture::PixelBufferDescriptor(
                reinterpret_cast<uint8_t*>(texels), size_t(texture->getWidth()) * texture->getHeight() * 4,
                f::Texture::Format::RGBA, f::Texture::Type::UBYTE,
                [](void* memory, size_t, void*) { WebPFree(memory); }));
            // The TextureProvider contract promises mipmaps; PNG and JPEG get them the same way.
            texture->generateMipmaps(*engine_);
        }
        // Popped entries at the front are done; others wait for a later call.
        size_t done = 0;
        while (done < textures_.size() && textures_[done]->state == State::popped) ++done;
        textures_.erase(textures_.begin(), textures_.begin() + ptrdiff_t(done));
    }

    void waitForCompletion() override {
        for (auto& info : textures_) if (info->job.valid()) info->job.wait();
    }

    void cancelDecoding() override {
        waitForCompletion();
        for (auto& info : textures_) {
            if (info->state != State::decoding) continue;
            const intptr_t texels = info->texels.load();
            if (texels != pending && texels != error) WebPFree(reinterpret_cast<void*>(texels));
            info->state = State::popped;
        }
    }

    const char* getPushMessage() const override { return push_message_.empty() ? nullptr : push_message_.c_str(); }
    const char* getPopMessage() const override { return pop_message_.empty() ? nullptr : pop_message_.c_str(); }
    size_t getPushedCount() const override { return pushed_; }
    size_t getPoppedCount() const override { return popped_; }
    size_t getDecodedCount() const override { return decoded_; }

private:
    static constexpr intptr_t pending = 0, error = 1;
    enum class State { decoding, ready, popped };
    struct Info {
        f::Texture* texture = nullptr;
        State state = State::decoding;
        // Written by the decoder job: pending, error, or the libwebp texel pointer.
        std::atomic<intptr_t> texels{pending};
        std::vector<uint8_t> source;
        std::future<void> job;
    };
    f::Engine* engine_;
    std::vector<std::unique_ptr<Info>> textures_;
    std::string push_message_, pop_message_;
    size_t pushed_ = 0, popped_ = 0, decoded_ = 0;
};
}

g::TextureProvider* create_webp_provider(f::Engine* engine) {
    // A later SDK can include WebP. Then this provider is redundant, so tell the maintainer once.
    static const bool checked = [engine] {
        if (std::unique_ptr<g::TextureProvider>(g::createWebpProvider(engine)))
            utils::slog.w << "filly: the Filament SDK has a WebP provider; filly's own provider is redundant"
                          << utils::io::endl;
        return true;
    }();
    (void)checked;
    return new WebpProvider(engine);
}
}
