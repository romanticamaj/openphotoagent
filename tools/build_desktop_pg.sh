#!/usr/bin/env bash
# pg pack for the desktop bundle (08 §2.3 D4): PostgreSQL 17 + pgvector 0.8, only the binaries the app runs.
#   macOS / Linux: built from the official source tarball (Linux on ubuntu-22.04 = glibc 2.35 baseline).
#   Windows: the EDB binary zip (PostgreSQL licence) + pgvector built with nmake (needs the MSVC dev shell).
# pgvector is always compiled by us and smoke-tested (CREATE EXTENSION, HNSW, kNN) before packing.
# Usage: tools/build_desktop_pg.sh <out-dir>     CI only (desktop-build.yml); needs network to ftp.postgresql.org.
set -euo pipefail
OUT="${1:?out dir}"
PG_VERSION="${PG_VERSION:-17.6}"
PGVECTOR_REF="${PGVECTOR_REF:-v0.8.1}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    curl -fsSL "https://get.enterprisedb.com/postgresql/postgresql-${PG_VERSION}-1-windows-x64-binaries.zip" -o "$WORK/pg.zip"
    unzip -q "$WORK/pg.zip" -d "$WORK"
    PREFIX="$WORK/pgsql"
    git clone -q --depth 1 --branch "$PGVECTOR_REF" https://github.com/pgvector/pgvector.git "$WORK/pgvector"
    (cd "$WORK/pgvector" && PGROOT="$(cygpath -w "$PREFIX")" nmake //NOLOGO //F Makefile.win && PGROOT="$(cygpath -w "$PREFIX")" nmake //NOLOGO //F Makefile.win install)
    EXE=.exe ;;
  *)
    curl -fsSL "https://ftp.postgresql.org/pub/source/v${PG_VERSION}/postgresql-${PG_VERSION}.tar.bz2" -o "$WORK/pg.tar.bz2"
    tar -xjf "$WORK/pg.tar.bz2" -C "$WORK"
    PREFIX="$WORK/prefix"
    (cd "$WORK/postgresql-${PG_VERSION}" && ./configure --prefix="$PREFIX" --without-icu --without-readline --without-zlib --disable-rpath >/dev/null && make -s -j"$(getconf _NPROCESSORS_ONLN)" && make -s install)
    git clone -q --depth 1 --branch "$PGVECTOR_REF" https://github.com/pgvector/pgvector.git "$WORK/pgvector"
    (cd "$WORK/pgvector" && make -s PG_CONFIG="$PREFIX/bin/pg_config" OPTFLAGS="" && make -s PG_CONFIG="$PREFIX/bin/pg_config" install)
    EXE= ;;
esac

# smoke test before packing
"$PREFIX/bin/initdb$EXE" -D "$WORK/smoke" -U opagent -A trust -E UTF8 --locale-provider=builtin --builtin-locale=C.UTF-8 --locale=C >/dev/null
"$PREFIX/bin/pg_ctl$EXE" -D "$WORK/smoke" -o "-p 54329 -c listen_addresses=127.0.0.1" -l "$WORK/smoke.log" -w start >/dev/null
"$PREFIX/bin/psql$EXE" -h 127.0.0.1 -p 54329 -U opagent -d postgres -v ON_ERROR_STOP=1 -q -c \
  "CREATE EXTENSION vector; CREATE TABLE v(id int, e vector(3)); INSERT INTO v VALUES (1,'[1,0,0]'),(2,'[0,1,0]'),(3,'[0.9,0.1,0]'); CREATE INDEX ON v USING hnsw (e vector_cosine_ops);" \
  -c "SELECT string_agg(id::text, ',') FROM (SELECT id FROM v ORDER BY e <=> '[1,0,0]' LIMIT 2) s" -t | grep -q "1,3"
"$PREFIX/bin/pg_ctl$EXE" -D "$WORK/smoke" -m fast stop >/dev/null

# pack: only what the app runs (postgres, initdb, pg_ctl, pg_dump, pg_restore, pg_upgrade) + libs + share
mkdir -p "$OUT/bin"
for b in postgres initdb pg_ctl pg_dump pg_restore pg_upgrade; do cp "$PREFIX/bin/$b$EXE" "$OUT/bin/"; done
[ -z "$EXE" ] || cp "$PREFIX"/bin/*.dll "$OUT/bin/"
cp -R "$PREFIX/lib" "$OUT/lib"
rm -rf "$OUT/lib/pgxs" "$OUT/lib/pkgconfig" "$OUT"/lib/*.a
# embedded-SQL (ecpg) client libraries: never used, and linuxdeploy cannot resolve their libpgtypes dependency
rm -f "$OUT"/lib/libecpg* "$OUT"/lib/libpgtypes* "$OUT"/bin/ecpg*
cp -R "$PREFIX/share" "$OUT/share"
rm -rf "$OUT/share/doc" "$OUT/share/man"
[ -n "$EXE" ] || find "$OUT/bin" "$OUT/lib" -type f \( -perm -u+x -o -name '*.so*' -o -name '*.dylib' \) -exec strip -S {} + 2>/dev/null || true
du -sh "$OUT"
