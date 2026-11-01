/* Development-only JDK compiler/executor for exact source-array model fixtures.
 * JLS21 10 / 15.10 / 15.26. No PCC compiler or runtime dependency. */
import java.io.ByteArrayOutputStream;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import javax.tools.ToolProvider;

public final class ArrayModelReference {
    public static void main(String[] args) throws Exception {
        Path root = Path.of(args[0]);
        String level = args[1];
        var compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) throw new IllegalStateException("selected JDK lacks jdk.compiler");
        for (String row : Files.readAllLines(root.resolve("cases.tsv"), StandardCharsets.UTF_8)) {
            String[] fields = row.split("\t", -1);
            String name = fields[0], kind = fields[1];
            Path directory = root.resolve(name);
            Path classes = directory.resolve("classes");
            Files.createDirectory(classes);
            var command = new ArrayList<String>();
            for (String value : new String[]{"-proc:none", "-encoding", "UTF-8", "-source", level,
                    "-target", level, "-classpath", classes.toString(), "-d", classes.toString(),
                    directory.resolve("ArrayExample.java").toString()}) command.add(value);
            if (kind.equals("run")) command.add(directory.resolve("PccArrayObserve.java").toString());
            var out = new ByteArrayOutputStream();
            var err = new ByteArrayOutputStream();
            Files.write(directory.resolve("compiler-arguments.txt"), command, StandardCharsets.UTF_8);
            int status = compiler.run(null, out, err, command.toArray(new String[0]));
            Files.write(directory.resolve("compiler.stdout.bin"), out.toByteArray());
            Files.write(directory.resolve("compiler.stderr.bin"), err.toByteArray());
            Files.writeString(directory.resolve("compiler-status.txt"), Integer.toString(status));
            if (kind.equals("reject")) {
                if (status == 0) throw new AssertionError("unexpected reference acceptance: " + name);
            } else {
                if (status != 0) throw new AssertionError("reference rejected: " + name);
                if (kind.equals("run")) {
                    try (var loader = new URLClassLoader(new URL[]{classes.toUri().toURL()}, ClassLoader.getPlatformClassLoader())) {
                        Object result = loader.loadClass("PccArrayObserve").getMethod("value").invoke(null);
                        // Actual semantic value, not a fabricated per-case raw process stream.
                        Files.writeString(directory.resolve("runtime-value.txt"), result.toString());
                        if (!result.equals(Integer.valueOf(42))) throw new AssertionError("reference value: " + name + "=" + result);
                    }
                }
            }
            System.out.println(name + "\t" + kind + "\t" + status);
        }
    }
}
