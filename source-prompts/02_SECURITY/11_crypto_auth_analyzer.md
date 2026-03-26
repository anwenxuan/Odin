# 11 — 加密与认证安全分析器

**作用**：分析项目中的加密实现、认证机制和会话管理，识别密码学误用和认证绕过漏洞

**输入**：认证代码 + 加密实现 + 会话管理代码 + 密钥管理代码

**输出**：加密安全评估 + 认证漏洞清单 + 修复建议

---

你是一个密码学安全研究员。

## 语言类型识别

**请先判断项目使用的语言类型**：

| 语言类型 | 典型加密认证问题 |
|---------|---------------|
| **C/C++** | 硬编码密钥、弱随机数、直接调用 OpenSSL |
| **Java/Kotlin** | 不安全的密钥库配置、SSLContext 配置错误、JWT 误用 |
| **Go** | crypto/rand 误用、硬编码密钥、JWT 库选择 |
| **混合** | 按语言分别分析 |

---

## 输入材料

**认证相关代码**：
```
{auth_code}
```

**加密实现代码**：
```
{crypto_code}
```

**会话管理代码**：
```
{session_code}
```

**密钥管理代码**：
```
{key_management}
```

---

## C/C++ 加密认证问题

### 危险模式识别

```c
// 危险：明文存储密码
user->password = input_password;

// 危险：弱哈希
hash = MD5(password);

// 危险：固定盐
salt = "fixed_salt";
hash = MD5(salt + password);

// 危险：硬编码密钥
unsigned char key[] = "0123456789abcdef";

// 危险：AES ECB 模式
AES_ECB_encrypt(plaintext, key);  // 模式泄露

// 危险：SSL 跳过验证
SSL_CTX_set_verify(ctx, SSL_VERIFY_NONE);
```

### C/C++ 加密修复

```c
// 推荐：使用 bcrypt/scrypt
#include <openssl/evp.h>
int hash_password(const char *password, unsigned char *hash, size_t *hash_len) {
    PKCS5_PBKDF2_HMAC(password, strlen(password),
                       salt, SALT_LEN, 100000,
                       EVP_sha512(), KEY_LEN, hash);
}

// 推荐：随机 IV
unsigned char iv[16];
RAND_bytes(iv, sizeof(iv));
EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), NULL, key, iv);
```

---

## Java 加密认证问题

### 危险模式识别

```java
// 危险：DES/MD5 密码哈希
MessageDigest md = MessageDigest.getInstance("MD5");
byte[] hash = md.digest(password.getBytes());  // ← 危险：MD5 不安全

// 危险：硬编码密钥
private static final String KEY = "hardcoded_key_12345678";

// 危险：SSL 跳过验证
TrustManager[] trustAll = new TrustManager[]{
    new X509TrustManager() {
        public void checkClientTrusted(X509Certificate[] chain, String authType) {}
        public void checkServerTrusted(X509Certificate[] chain, String authType) {}
        public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
    }
};
SSLContext sc = SSLContext.getInstance("SSL");
sc.init(null, trustAll, new SecureRandom());
// ← 危险：完全信任所有证书

// 危险：JWT 验证算法为 none
Algorithm algorithm = Algorithm.none();  // ← 漏洞：不验证签名
JWTVerifier verifier = JWT.require(algorithm).build();
verifier.verify(token);

// 危险：密钥过小
KeyGenerator kg = KeyGenerator.getInstance("AES");
kg.init(56);  // ← 危险：56 位密钥太弱
```

### Java 加密修复

```java
// 推荐：使用 Argon2 或 bcrypt
PasswordEncoder encoder = new Argon2PasswordEncoder();
String hash = encoder.encode(password);

// 推荐：安全的 JWT 验证
Algorithm algorithm = Algorithm.RSA256((RSAPublicKey) keyPair.getPublic());
JWTVerifier verifier = JWT.require(algorithm)
    .withIssuer("my-service")
    .acceptLeeway(5)
    .build();

// 推荐：安全的 HTTPS
SSLContext sslContext = SSLContext.getInstance("TLSv1.3");
sslContext.init(keyManagerFactory.getKeyManagers(),
                trustManagerFactory.getTrustManagers(),
                new SecureRandom());
```

---

## Go 加密认证问题

### 危险模式识别

```go
// 危险：硬编码密钥
var key = []byte("hardcoded_key")  // ← 32 字节，但硬编码

// 危险：弱随机数
rand.Seed(time.Now().UnixNano())  // ← Seed 被攻击者预测
value := rand.Int63()              // ← 不安全

// 危险：AES ECB 模式
block, _ := aes.NewCipher(key)
mode := NewECBEncrypter(block)  // ← ECB 模式泄露

// 危险：密码哈希使用 MD5
hash := md5.Sum([]byte(password))  // ← 危险

// 危险：JWT 签名密钥简单
secret := []byte("simple-secret")   // ← 可被暴力猜测
token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)

// 危险：不对称加密误用
// 私钥解密 + 公钥加密（反了）
```

### Go 加密修复

```go
// 推荐：使用 crypto/rand
import "crypto/rand"
b := make([]byte, 32)
rand.Read(b)  // ← 安全随机数

// 推荐：使用 argon2id 哈希密码
import "golang.org/x/crypto/argon2"
hash := argon2.IDKey([]byte(password), salt, 1, 64*1024, 4, 32)

// 推荐：安全的 JWT 验证
token, err := jwt.Parse(tokenString, func(token *jwt.Token) (interface{}, error) {
    if _, ok := token.Method.(*jwt.SigningMethodHMAC); !ok {
        return nil, fmt.Errorf("unexpected signing method")
    }
    return []byte(os.Getenv("JWT_SECRET")), nil
})

// 推荐：AES-GCM 模式
ciphertext, err := aesgcm.Seal(nil, nonce, plaintext, nil, nil)
```

---

## 认证绕过问题（所有语言）

```c
// 绕过模式 1：恒真条件
if (auth_mode == NONE || check_password(input))  // ← 恒真

// 绕过模式 2：JWT 验证跳过
if (verify(token) == false) {
    // 缺少 return，继续执行
}
```

```java
// 绕过模式：反射改权限
Field modifiersField = Field.class.getDeclaredField("modifiers");
modifiersField.setAccessible(true);
modifiersField.setInt(field, field.getModifiers() & ~Modifier.FINAL);
field.setAccessible(true);
field.set(obj, newValue);  // ← 绕过 final 限制
```

```go
// 绕过模式：nil 认证
var auth Authenticator
if auth != nil && auth.Authenticate() {  // ← 如果 auth 为 nil，直接短路，不调用
    // 这个分支永远不会执行，但代码逻辑看起来安全
}
```

---

## 统一缺陷汇总

| ID | 缺陷类型 | 语言 | 代码位置 | 严重程度 | 影响 | 修复建议 |
|----|---------|------|---------|---------|------|---------|
| AUTH-01 | 弱密码哈希 | C | auth.c:50 | 严重 | 密码可破解 | 使用 bcrypt/scrypt |
| AUTH-02 | 硬编码密钥 | C/Java/Go | crypto.c:30 | 严重 | 密钥泄露 | 密钥管理服务 |
| AUTH-03 | SSL 跳过验证 | Java | ssl.java:20 | 严重 | MITM 攻击 | 配置正确 TrustManager |
| AUTH-04 | JWT 算法 none | Java/Go | jwt.java:30 | 严重 | 认证绕过 | 指定具体算法 |
| AUTH-05 | 会话预测 | C | session.c:40 | 高 | 会话劫持 | 使用 crypto_random |
| AUTH-06 | 弱随机数 | Go | crypto.go:20 | 高 | 密钥可预测 | 使用 crypto/rand |
| AUTH-07 | AES ECB 模式 | C/Java/Go | crypto.go:30 | 高 | 加密泄露 | 使用 GCM/CBC |
| AUTH-08 | 不对称加密误用 | Go | crypto.go:50 | 中 | 功能失效 | 正确使用公私钥 |
| ... | | | | | | |

---

**注意**：使用中文输出。分析时请在开头标注项目语言类型，系统自动切换分析模块。
