/* Dev-only batched JDK compilation of unchanged narrow-primitive originals.
 * JLS21 5 and 15. It never executes PCC code or replaces a production owner. */
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import javax.tools.ToolProvider;

public final class NarrowReference {
    public static void main(String[] args) throws Exception {
        Path root = Path.of(args[0]);
        var compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) throw new IllegalStateException("selected JDK lacks jdk.compiler");
        for (String row : Files.readAllLines(root.resolve("cases.tsv"), StandardCharsets.UTF_8)) {
            String[] fields = row.split("\t", -1);
            Path directory = root.resolve(fields[0]);
            Path classes = directory.resolve("classes");
            Files.createDirectory(classes);
            var command = new ArrayList<String>();
            for (String value : new String[]{"-proc:none", "-encoding", "UTF-8", "-source", args[1],
                    "-target", args[1], "-classpath", classes.toString(), "-d", classes.toString(),
                    directory.resolve(fields[2]).toString()}) command.add(value);
            if (fields[1].equals("run")) command.add(directory.resolve("PccNarrowObserve.java").toString());
            var out = new ByteArrayOutputStream();
            var err = new ByteArrayOutputStream();
            Files.write(directory.resolve("compiler-arguments.txt"), command, StandardCharsets.UTF_8);
            int status = compiler.run(null, out, err, command.toArray(new String[0]));
            Files.write(directory.resolve("compiler.stdout.bin"), out.toByteArray());
            Files.write(directory.resolve("compiler.stderr.bin"), err.toByteArray());
            Files.writeString(directory.resolve("compiler-status.txt"), Integer.toString(status));
            System.out.println(fields[0] + "\t" + fields[1] + "\t" + status);
        }
    }
}
